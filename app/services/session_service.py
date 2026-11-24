
"""Teacher-side attendance sessions: start, QR issue, inspect, close.

The database is authoritative. A session is live only while its status
is 'active' and session_ends_at is in the future.

QR policy:
* Exactly one QR token is issued per attendance session.
* The encrypted token is reused when the teacher reloads the page.
* An expired QR is never regenerated.
* Legacy sessions with non-recoverable QR tokens cannot receive another QR.
"""
import math
import secrets

from flask import current_app

from app import clock
from app.clock import iso
from app.db import IntegrityError, execute, is_duplicate_key, query_all, query_one, transaction
from app.services import audit_service, qr_service
from app.services.errors import ServiceError

_SESSION_SELECT = (
    "SELECT s.id, s.status, s.started_at, s.session_ends_at, s.closed_at, s.qr_validity_seconds, "
    "       s.geofence_enabled, s.geo_latitude, s.geo_longitude, s.geo_radius_m, s.geo_min_accuracy_m, "
    "       s.classroom_id, cr.name AS classroom_name, ts.id AS assignment_id, "
    "       ts.subject_id, ts.class_id, ts.semester_id, sub.code AS subject_code, "
    "       sub.name AS subject_name, c.name AS class_name, c.section "
    "FROM attendance_sessions s "
    "JOIN teacher_subjects ts ON ts.id = s.assignment_id "
    "JOIN subjects sub ON sub.id = ts.subject_id "
    "JOIN classes c ON c.id = ts.class_id "
    "LEFT JOIN classrooms cr ON cr.id = s.classroom_id "
)


# ------------------------------------------------------------ validation
def _int(data, key, lo, hi, default, errors):
    value = data.get(key, default)
    if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
        errors[key] = "Enter a whole number."
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors[key] = "Enter a whole number."
        return None
    if not lo <= number <= hi:
        errors[key] = f"Must be between {lo} and {hi}."
        return None
    return number


def _float(data, key, lo, hi, errors):
    value = data.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not lo <= value <= hi
    ):
        errors[key] = f"Enter a number between {lo} and {hi}."
        return None
    return float(value)


def validate_start_request(data):
    """Return cleaned settings or raise ServiceError('validation_error')."""
    cfg = current_app.config
    errors = {}

    cleaned = {
        "assignment_id": _int(data, "assignment_id", 1, 2**31, None, errors),
        "qr_validity_seconds": _int(
            data, "qr_validity_seconds", 15, 600,
            cfg["QR_VALIDITY_SECONDS"], errors
        ),
        "duration_minutes": _int(
            data, "duration_minutes", 1, 240,
            cfg["SESSION_DEFAULT_MINUTES"], errors
        ),
        "geofence_enabled": data.get("geofence_enabled", True),
        "classroom_id": None,
        "latitude": None,
        "longitude": None,
        "radius_m": None,
        "min_accuracy_m": None,
    }

    if not isinstance(cleaned["geofence_enabled"], bool):
        errors["geofence_enabled"] = "Must be true or false."

    if data.get("classroom_id") not in (None, ""):
        cleaned["classroom_id"] = _int(
            data, "classroom_id", 1, 2**31, None, errors
        )

    if cleaned["geofence_enabled"] is True:
        cleaned["radius_m"] = _int(data, "radius_m", 5, 1000, 50, errors)
        cleaned["min_accuracy_m"] = _int(
            data, "min_accuracy_m", 5, 500, 50, errors
        )

        if data.get("latitude") is not None or data.get("longitude") is not None:
            cleaned["latitude"] = _float(data, "latitude", -90, 90, errors)
            cleaned["longitude"] = _float(data, "longitude", -180, 180, errors)

    if errors:
        raise ServiceError(
            "validation_error",
            "Please correct the highlighted fields.",
            422,
            fields=errors,
        )

    return cleaned


# ---------------------------------------------------------------- helpers
def _public(row, now):
    live = row["status"] == "active" and row["session_ends_at"] > now
    state = (
        "closed"
        if row["status"] == "closed"
        else ("active" if live else "expired")
    )

    return {
        "id": row["id"],
        "state": state,
        "assignment_id": row["assignment_id"],
        "subject_code": row["subject_code"],
        "subject_name": row["subject_name"],
        "class_name": f"{row['class_name']} {row['section']}",
        "classroom": row["classroom_name"],
        "started_at": iso(row["started_at"]),
        "ends_at": iso(row["session_ends_at"]),
        "closed_at": iso(row["closed_at"]),
        "qr_validity_seconds": row["qr_validity_seconds"],
        "geofence_enabled": bool(row["geofence_enabled"]),
        "geofence": None if not row["geofence_enabled"] else {
            "latitude": float(row["geo_latitude"]),
            "longitude": float(row["geo_longitude"]),
            "radius_m": row["geo_radius_m"],
            "max_accuracy_m": row["geo_min_accuracy_m"],
        },
        "server_time": iso(now),
    }


def _owned_session(teacher_id, session_id, for_update=False):
    sql = _SESSION_SELECT + "WHERE s.id = %s AND ts.teacher_id = %s"

    if for_update:
        sql += " FOR UPDATE OF s"

    return query_one(sql, (session_id, teacher_id))


def _qr_result(token, issued_at, expires_at, validity_seconds, now):
    """Build the same API response for a newly issued or recovered QR."""
    payload = qr_service.build_payload(token)

    return {
        "payload": payload,
        "image": qr_service.render_png_data_uri(payload),
        "issued_at": iso(issued_at),
        "expires_at": iso(expires_at),
        "seconds_remaining": max(0, int((expires_at - now).total_seconds())),
        "validity_seconds": validity_seconds,
        "server_time": iso(now),
    }


# ----------------------------------------------------------------- lookups
def list_assignments(teacher_id):
    """Subjects/classes this teacher may start sessions for (current semester only)."""
    return query_all(
        "SELECT ts.id AS assignment_id, sub.code AS subject_code, sub.name AS subject_name, "
        "       c.id AS class_id, c.name AS class_name, c.section, sem.name AS semester_name "
        "FROM teacher_subjects ts "
        "JOIN subjects sub ON sub.id = ts.subject_id "
        "JOIN classes c ON c.id = ts.class_id "
        "JOIN semesters sem ON sem.id = ts.semester_id "
        "WHERE ts.teacher_id = %s AND sem.is_current = 1 ORDER BY sub.code, c.name",
        (teacher_id,),
    )


def list_classrooms():
    return query_all(
        "SELECT id, name, building, latitude, longitude, default_radius_m "
        "FROM classrooms ORDER BY name"
    )


def list_active_sessions(teacher_id):
    now = clock.now()

    rows = query_all(
        _SESSION_SELECT
        + "WHERE ts.teacher_id = %s AND s.status = 'active' "
        "AND s.session_ends_at > %s ORDER BY s.started_at DESC",
        (teacher_id, now),
    )

    return [_public(row, now) for row in rows]


# --------------------------------------------------------------- start/close
def start_session(teacher_id, cleaned, ip):
    now = clock.now()

    with transaction():
        assignment = query_one(
            "SELECT ts.id, sem.is_current FROM teacher_subjects ts "
            "JOIN semesters sem ON sem.id = ts.semester_id "
            "WHERE ts.id = %s AND ts.teacher_id = %s",
            (cleaned["assignment_id"], teacher_id),
        )

        if assignment is None:
            raise ServiceError(
                "not_assigned",
                "You are not assigned to that subject and class.",
                403,
            )

        if not assignment["is_current"]:
            raise ServiceError(
                "semester_not_current",
                "That assignment is not in the current semester.",
                409,
            )

        latitude = cleaned["latitude"]
        longitude = cleaned["longitude"]

        if cleaned["classroom_id"] is not None:
            room = query_one(
                "SELECT latitude, longitude FROM classrooms WHERE id = %s",
                (cleaned["classroom_id"],),
            )

            if room is None:
                raise ServiceError(
                    "validation_error",
                    "Unknown classroom.",
                    422,
                    fields={"classroom_id": "Unknown classroom."},
                )

            if latitude is None:
                latitude = float(room["latitude"])
                longitude = float(room["longitude"])

        if cleaned["geofence_enabled"] and (
            latitude is None or longitude is None
        ):
            raise ServiceError(
                "validation_error",
                "Choose a classroom or enter the classroom's coordinates.",
                422,
                fields={"latitude": "Required when the geofence is on."},
            )

        execute(
            "UPDATE attendance_sessions "
            "SET status = 'closed', closed_at = session_ends_at "
            "WHERE assignment_id = %s AND status = 'active' "
            "AND session_ends_at <= %s",
            (cleaned["assignment_id"], now),
        )

        if query_one(
            "SELECT id FROM attendance_sessions "
            "WHERE assignment_id = %s AND status = 'active' LIMIT 1",
            (cleaned["assignment_id"],),
        ):
            raise ServiceError(
                "session_already_active",
                "A session is already active for this subject and class. Close it first.",
                409,
            )

        try:
            session_id = execute(
                "INSERT INTO attendance_sessions "
                "(assignment_id, classroom_id, status, started_at, session_ends_at, "
                "qr_validity_seconds, geofence_enabled, geo_latitude, geo_longitude, "
                "geo_radius_m, geo_min_accuracy_m) "
                "VALUES (%s, %s, 'active', %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    cleaned["assignment_id"],
                    cleaned["classroom_id"],
                    now,
                    now + _minutes(cleaned["duration_minutes"]),
                    cleaned["qr_validity_seconds"],
                    1 if cleaned["geofence_enabled"] else 0,
                    latitude if cleaned["geofence_enabled"] else None,
                    longitude if cleaned["geofence_enabled"] else None,
                    cleaned["radius_m"],
                    cleaned["min_accuracy_m"],
                ),
            ).lastrowid

        except IntegrityError as exc:
            if is_duplicate_key(exc, "uq_one_active_session_per_assignment"):
                raise ServiceError(
                    "session_already_active",
                    "A session is already active for this subject and class.",
                    409,
                ) from None
            raise

        audit_service.log(
            "attendance.session_start",
            user_id=teacher_id,
            entity_type="attendance_session",
            entity_id=session_id,
            ip=ip,
            details={
                "assignment_id": cleaned["assignment_id"],
                "geofence": bool(cleaned["geofence_enabled"]),
            },
        )

    return get_session(teacher_id, session_id)


def _minutes(n):
    from datetime import timedelta
    return timedelta(minutes=n)


def close_session(teacher_id, session_id, ip):
    now = clock.now()

    with transaction():
        row = _owned_session(teacher_id, session_id, for_update=True)

        if row is None:
            raise ServiceError("not_found", "Session not found.", 404)

        if row["status"] == "active":
            execute(
                "UPDATE attendance_sessions SET status = 'closed', closed_at = %s "
                "WHERE id = %s",
                (now, session_id),
            )

            execute(
                "UPDATE attendance_qr_tokens "
                "SET expires_at = LEAST(expires_at, %s) WHERE session_id = %s",
                (now, session_id),
            )

            audit_service.log(
                "attendance.session_close",
                user_id=teacher_id,
                entity_type="attendance_session",
                entity_id=session_id,
                ip=ip,
            )

    return get_session(teacher_id, session_id)


# ------------------------------------------------------------------ detail
def get_session(teacher_id, session_id, include_records=False):
    now = clock.now()
    row = _owned_session(teacher_id, session_id)

    if row is None:
        raise ServiceError("not_found", "Session not found.", 404)

    data = _public(row, now)

    data["present_count"] = query_one(
        "SELECT COUNT(*) AS c FROM attendance_records WHERE session_id = %s",
        (session_id,),
    )["c"]

    data["enrolled_count"] = query_one(
        "SELECT COUNT(*) AS c FROM enrollments "
        "WHERE subject_id = %s AND class_id = %s "
        "AND semester_id = %s AND status = 'active'",
        (row["subject_id"], row["class_id"], row["semester_id"]),
    )["c"]

    if include_records:
        data["records"] = [
            {
                "student": record["full_name"],
                "roll_number": record["roll_number"],
                "marked_at": iso(record["marked_at"]),
                "distance_m": (
                    None
                    if record["distance_m"] is None
                    else float(record["distance_m"])
                ),
            }
            for record in query_all(
                "SELECT u.full_name, sp.roll_number, ar.marked_at, ar.distance_m "
                "FROM attendance_records ar "
                "JOIN users u ON u.id = ar.student_id "
                "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
                "WHERE ar.session_id = %s ORDER BY ar.marked_at, u.full_name",
                (session_id,),
            )
        ]

    return data


# --------------------------------------------------------------- one-time QR
# --------------------------------------------------------------- QR issue / rotation
def issue_qr(teacher_id, session_id):
    """Issue a QR, recover the current QR, or rotate an expired QR.

    Policy:
    * Only one unexpired QR is active per session.
    * Repeated requests recover the same unexpired encrypted QR.
    * Once the latest QR expires, a fresh QR may be issued.
    * Previous tokens remain stored but cannot be used after expiry.
    * No QR can be issued after the attendance session ends.
    """
    now = clock.now()

    with transaction():
        # Lock the session row to serialize simultaneous QR requests.
        row = _owned_session(teacher_id, session_id, for_update=True)

        if row is None:
            raise ServiceError("not_found", "Session not found.", 404)

        if row["status"] != "active" or row["session_ends_at"] <= now:
            raise ServiceError(
                "session_closed",
                "This session is no longer accepting attendance.",
                409,
            )

        # Get the latest QR issued for this session.
        existing = query_one(
            "SELECT id, token_hash, encrypted_token, issued_at, expires_at "
            "FROM attendance_qr_tokens "
            "WHERE session_id = %s ORDER BY id DESC LIMIT 1",
            (session_id,),
        )

        if existing is not None and existing["expires_at"] > now:
            # An unexpired QR must be recovered, not replaced.
            if not existing["encrypted_token"]:
                raise ServiceError(
                    "qr_already_issued",
                    "A QR was already issued for this session and cannot be recovered. "
                    "Wait for it to expire before requesting another QR.",
                    409,
                )

            token = qr_service.decrypt_token(existing["encrypted_token"])

            # Verify the decrypted token against its stored SHA-256 hash.
            if not secrets.compare_digest(
                qr_service.hash_token(token),
                existing["token_hash"],
            ):
                raise ServiceError(
                    "qr_unavailable",
                    "The saved QR code could not be verified.",
                    409,
                )

            return _qr_result(
                token,
                existing["issued_at"],
                existing["expires_at"],
                row["qr_validity_seconds"],
                now,
            )

        # No QR exists, or the latest QR has expired.
        # Issue a new token without modifying previous token records.
        validity = min(
            row["qr_validity_seconds"],
            int((row["session_ends_at"] - now).total_seconds()),
        )

        if validity <= 0:
            raise ServiceError(
                "session_closed",
                "This session is no longer accepting attendance.",
                409,
            )

        token, expires_at = qr_service.issue_token(
            session_id,
            validity,
            now,
        )

    return _qr_result(
        token,
        now,
        expires_at,
        row["qr_validity_seconds"],
        now,
    )