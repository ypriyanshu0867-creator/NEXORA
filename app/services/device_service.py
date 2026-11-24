
"""Device registration and attendance-aware account-switch protection.

Students only:
- A browser receives a random device cookie; only its SHA-256 hash is stored.
- Switching accounts is restricted only while a relevant attendance session
  is active for an enrolled student.
- The restriction lasts DEVICE_COOLDOWN_SECONDS (normally 180 seconds).
- Closing or ending the relevant session immediately removes the restriction.
- One device may still mark attendance for only one student per session.
- Device state changes are serialised with a database row lock.
"""
import hashlib
import math
import re
import secrets
from dataclasses import dataclass

from flask import current_app, request, session

from app import clock
from app.db import execute, query_one
from app.services.errors import ServiceError

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
TOUCH_INTERVAL_SECONDS = 60


@dataclass
class Decision:
    allowed: bool
    code: str = ""
    message: str = ""
    retry_after: int = 0


# ---------------------------------------------------------------- cookie
def hash_token(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def cookie_hash():
    """SHA-256 of the request's valid device cookie, or None."""
    token = request.cookies.get(current_app.config["DEVICE_COOKIE_NAME"], "")
    return hash_token(token) if TOKEN_RE.match(token) else None


def ensure_cookie(response):
    """Issue a device cookie if the request did not carry a valid one."""
    if cookie_hash() is not None:
        return response

    cfg = current_app.config
    response.set_cookie(
        cfg["DEVICE_COOKIE_NAME"],
        secrets.token_urlsafe(32),
        max_age=cfg["DEVICE_COOKIE_MAX_AGE_DAYS"] * 86400,
        httponly=True,
        samesite="Lax",
        secure=cfg["SESSION_COOKIE_SECURE"],
        path="/",
    )
    return response


# ---------------------------------------------------- attendance check
def _has_active_attendance_session(student_id, now=None):
    """Return True only for an active session in which this student is enrolled."""
    if student_id is None:
        return False

    now = now or clock.now()

    row = query_one(
        "SELECT s.id FROM attendance_sessions s "
        "JOIN teacher_subjects ts ON ts.id = s.assignment_id "
        "JOIN enrollments e ON e.subject_id = ts.subject_id "
        "AND e.class_id = ts.class_id "
        "AND e.semester_id = ts.semester_id "
        "WHERE e.student_id = %s "
        "AND e.status = 'active' "
        "AND s.status = 'active' "
        "AND s.started_at <= %s "
        "AND s.session_ends_at > %s "
        "LIMIT 1",
        (student_id, now, now),
    )

    return row is not None


# ---------------------------------------------------- pure policy (tested)
def evaluate_login(
    row,
    user_id,
    now,
    cooldown_seconds,
    stale_seconds,
    attendance_active=True,
):
    """Decide a student login from the device's current state.

    attendance_active indicates whether a relevant attendance session exists
    for the previous student or the incoming student.

    Returns (Decision, column_updates, events).
    """
    active = row["active_user_id"]
    last_seen = row["last_seen_at"]
    stale_active = False

    # No relevant attendance session: allow switching and clear old cooldown.
    if not attendance_active:
        return (
            Decision(True),
            {
                "active_user_id": user_id,
                "last_user_id": user_id,
                "cooldown_until": None,
            },
            [("login", user_id, None)],
        )

    # Expire abandoned logins.
    if (
        active not in (None, user_id)
        and last_seen is not None
        and (now - last_seen).total_seconds() > stale_seconds
    ):
        active = None
        stale_active = True

    until = row["cooldown_until"]

    # An existing cooldown blocks a different student.
    if (
        until is not None
        and until > now
        and row["last_user_id"] not in (None, user_id)
    ):
        remaining = math.ceil((until - now).total_seconds())

        return (
            Decision(
                False,
                "device_cooldown",
                _cooldown_message(remaining),
                remaining,
            ),
            {},
            [("cooldown_blocked", user_id, None)],
        )

    previous_user = (
        active
        if active not in (None, user_id)
        else row["last_user_id"]
    )

    # A switch can happen either while the previous account is still active
    # or after logout when no cooldown was needed for the previous student,
    # but the incoming student has an active attendance session.
    switch_detected = (
        active not in (None, user_id)
        or (
            active is None
            and previous_user not in (None, user_id)
            and until is None
            and not stale_active
        )
    )

    if switch_detected:
        return (
            Decision(
                False,
                "device_cooldown",
                _cooldown_message(cooldown_seconds),
                cooldown_seconds,
            ),
            {
                "active_user_id": None,
                "last_user_id": previous_user,
                "cooldown_until": now + _seconds(cooldown_seconds),
            },
            [
                ("account_switch", user_id, f"previous_user={previous_user}"),
                ("cooldown_started", previous_user, None),
            ],
        )

    return (
        Decision(True),
        {
            "active_user_id": user_id,
            "last_user_id": user_id,
            "cooldown_until": None,
        },
        [("login", user_id, None)],
    )


def _seconds(n):
    from datetime import timedelta
    return timedelta(seconds=n)


def _cooldown_message(seconds):
    return (
        "This device was recently used by another account. "
        f"Please try again in {seconds} second(s)."
    )


# -------------------------------------------------------------- database
def _event(device_id, user_id, event_type, ip, detail=None):
    execute(
        "INSERT INTO device_events "
        "(device_id, user_id, event_type, ip_address, details, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (device_id, user_id, event_type, ip, detail, clock.now()),
    )


def on_student_login(device_hash, user_id, ip):
    """Call INSIDE a transaction, after the password was verified."""
    if device_hash is None:
        return Decision(
            False,
            "device_required",
            "Your browser did not send its device cookie. Reload the login page "
            "and make sure cookies are enabled, then sign in again.",
        )

    cfg = current_app.config
    now = clock.now()

    created = execute(
        "INSERT IGNORE INTO device_registrations "
        "(device_hash, first_seen_at, last_seen_at) "
        "VALUES (%s, %s, %s)",
        (device_hash, now, now),
    ).rowcount == 1

    row = query_one(
        "SELECT id, active_user_id, last_user_id, cooldown_until, last_seen_at "
        "FROM device_registrations WHERE device_hash = %s FOR UPDATE",
        (device_hash,),
    )

    if created:
        _event(row["id"], None, "register", ip)

    # Check both the previous device user and the incoming student.
    related_users = {
        uid
        for uid in (
            row["active_user_id"],
            row["last_user_id"],
            user_id,
        )
        if uid is not None
    }

    attendance_active = any(
        _has_active_attendance_session(uid, now)
        for uid in related_users
    )

    decision, updates, events = evaluate_login(
        row,
        user_id,
        now,
        cfg["DEVICE_COOLDOWN_SECONDS"],
        int(cfg["PERMANENT_SESSION_LIFETIME"].total_seconds()),
        attendance_active=attendance_active,
    )

    if updates:
        merged = {
            key: row[key]
            for key in (
                "active_user_id",
                "last_user_id",
                "cooldown_until",
            )
        }
        merged.update(updates)

        execute(
            "UPDATE device_registrations SET active_user_id = %s, "
            "last_user_id = %s, cooldown_until = %s, last_seen_at = %s "
            "WHERE id = %s",
            (
                merged["active_user_id"],
                merged["last_user_id"],
                merged["cooldown_until"],
                now,
                row["id"],
            ),
        )

    for event_type, event_user, detail in events:
        _event(row["id"], event_user, event_type, ip, detail)

    return decision


def on_logout(device_hash, user_id, ip):
    """Apply cooldown only if this student has a relevant active session."""
    if device_hash is None:
        return

    now = clock.now()

    row = query_one(
        "SELECT id, active_user_id, last_user_id "
        "FROM device_registrations "
        "WHERE device_hash = %s FOR UPDATE",
        (device_hash,),
    )

    if row is None or user_id not in (
        row["active_user_id"],
        row["last_user_id"],
    ):
        return

    attendance_active = _has_active_attendance_session(user_id, now)

    if attendance_active:
        until = now + _seconds(
            current_app.config["DEVICE_COOLDOWN_SECONDS"]
        )
    else:
        until = None

    execute(
        "UPDATE device_registrations SET active_user_id = NULL, "
        "last_user_id = %s, cooldown_until = %s, last_seen_at = %s "
        "WHERE id = %s",
        (user_id, until, now, row["id"]),
    )

    _event(row["id"], user_id, "logout", ip)

    if attendance_active:
        _event(row["id"], user_id, "cooldown_started", ip)


def status_for(device_hash, user_id):
    """Data for GET /api/device/status."""
    if device_hash is None:
        return {
            "registered": False,
            "cooldown_active": False,
            "cooldown_remaining": 0,
            "bound_to_you": False,
        }

    row = query_one(
        "SELECT last_user_id, cooldown_until "
        "FROM device_registrations WHERE device_hash = %s",
        (device_hash,),
    )

    if row is None:
        return {
            "registered": False,
            "cooldown_active": False,
            "cooldown_remaining": 0,
            "bound_to_you": False,
        }

    now = clock.now()
    remaining = 0

    if row["cooldown_until"] is not None and row["cooldown_until"] > now:
        if _has_active_attendance_session(row["last_user_id"], now):
            remaining = math.ceil(
                (row["cooldown_until"] - now).total_seconds()
            )

    return {
        "registered": True,
        "cooldown_active": remaining > 0,
        "cooldown_remaining": remaining,
        "bound_to_you": row["last_user_id"] == user_id,
    }


def check_for_marking(device_hash, student_id, session_id):
    """Device rules for attendance. Returns device_id or raises ServiceError."""
    if device_hash is None:
        raise ServiceError(
            "device_unregistered",
            "This browser has no device registration. "
            "Sign out, reload the login page and sign in again.",
            403,
        )

    row = query_one(
        "SELECT id, last_user_id FROM device_registrations "
        "WHERE device_hash = %s",
        (device_hash,),
    )

    if row is None or row["last_user_id"] != student_id:
        raise ServiceError(
            "device_unregistered",
            "This device is not registered to your account. "
            "Sign out and sign in again.",
            403,
            device_id=row["id"] if row else None,
        )

    other = query_one(
        "SELECT student_id FROM attendance_records "
        "WHERE session_id = %s AND device_id = %s "
        "AND student_id <> %s LIMIT 1",
        (session_id, row["id"], student_id),
    )

    if other:
        raise ServiceError(
            "device_reused",
            "Another student has already marked attendance from "
            "this device in this session.",
            403,
            device_id=row["id"],
        )

    return row["id"]


def touch_if_due(user_id):
    """Keep last_seen_at fresh for the signed-in student (at most once a minute)."""
    token_hash = cookie_hash()

    if token_hash is None:
        return

    now = clock.now()
    last = session.get("device_touch")

    if last is not None and (
        now.timestamp() - last
    ) < TOUCH_INTERVAL_SECONDS:
        return

    session["device_touch"] = now.timestamp()

    execute(
        "UPDATE device_registrations SET last_seen_at = %s "
        "WHERE device_hash = %s AND last_user_id = %s",
        (now, token_hash, user_id),
    )
