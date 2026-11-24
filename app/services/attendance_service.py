"""Central attendance-marking service.

Order of checks (cheapest and least revealing first):
 1 rate limit  2 QR format  3 token -> session lookup  4 session live
 5 token not expired  6 student enrolled  7 not already marked
 8 device policy  9 geofence  10 insert (UNIQUE key is the final guard)

Everything between 3 and 10 runs in ONE transaction. The session row is
read with FOR SHARE, so closing a session (which needs an exclusive lock)
waits for in-flight marks and later marks see it closed. The student's
identity always comes from the server-side session, never from the request.
Rejections are logged to attendance_security_events AFTER the transaction
rolled back, so the log survives.
"""
from flask import current_app

from app import clock
from app.clock import iso
from app.db import IntegrityError, execute, is_duplicate_key, query_one, transaction
from app.services import audit_service, device_service, geo_service, qr_service
from app.services.errors import ServiceError

# error code -> value of the attendance_security_events.event_type ENUM
EVENT_TYPE = {
    "qr_invalid": "qr_invalid", "qr_expired": "qr_expired", "session_closed": "session_closed",
    "not_enrolled": "not_enrolled", "already_marked": "duplicate",
    "device_unregistered": "device_unregistered", "device_reused": "device_reused",
    "device_cooldown": "device_cooldown", "location_denied": "location_denied",
    "location_missing": "location_missing", "location_invalid": "location_invalid",
    "location_inaccurate": "location_inaccurate", "outside_radius": "outside_radius",
    "rate_limited": "rate_limited",
}


def _log_rejection(exc, student_id, ip, device_id):
    event_type = EVENT_TYPE.get(exc.code)
    if event_type is None:
        return
    try:
        execute("INSERT INTO attendance_security_events (session_id, student_id, device_id, event_type, "
                "detail, ip_address, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (exc.session_id, student_id, exc.device_id or device_id, event_type,
                 exc.message[:255], ip, clock.now()))
    except Exception:                                   # logging must never hide the real answer
        current_app.logger.exception("Could not record attendance security event")


def _check_rate_limit(student_id):
    limit = current_app.config["ATTENDANCE_MAX_FAILURES_PER_MINUTE"]
    row = query_one("SELECT COUNT(*) AS c FROM attendance_security_events WHERE student_id = %s "
                    "AND event_type <> 'rate_limited' AND created_at > %s",
                    (student_id, clock.now() - _one_minute()))
    if row["c"] >= limit:
        raise ServiceError("rate_limited", "Too many failed attempts. Wait a minute and try again.",
                           429, retry_after=60)


def _one_minute():
    from datetime import timedelta
    return timedelta(seconds=60)


def mark_attendance(student, raw_qr, body, device_hash, ip):
    """Mark ``student`` (the authenticated user row) present. Returns a dict or raises ServiceError."""
    student_id = student["id"]
    try:
        _check_rate_limit(student_id)
        token_hash = qr_service.hash_token(qr_service.parse_payload(raw_qr))
        now = clock.now()
        with transaction():
            row = query_one(
                "SELECT t.expires_at AS token_expires_at, s.id AS session_id, ts.subject_id, ts.class_id, "
                "       ts.semester_id, sub.name AS subject_name "
                "FROM attendance_qr_tokens t "
                "JOIN attendance_sessions s ON s.id = t.session_id "
                "JOIN teacher_subjects ts ON ts.id = s.assignment_id "
                "JOIN subjects sub ON sub.id = ts.subject_id WHERE t.token_hash = %s", (token_hash,))
            if row is None:
                raise ServiceError("qr_invalid", "This QR code is not recognised.", 400)
            sid = row["session_id"]

            live = query_one("SELECT status, session_ends_at, geofence_enabled, geo_latitude, geo_longitude, "
                             "geo_radius_m, geo_min_accuracy_m FROM attendance_sessions "
                             "WHERE id = %s FOR SHARE", (sid,))
            if live["status"] != "active" or live["session_ends_at"] <= now:
                raise ServiceError("session_closed", "This attendance session has ended.", 409, session_id=sid)
            if row["token_expires_at"] <= now:
                raise ServiceError("qr_expired", "This QR code has expired. Scan the current code on the "
                                   "teacher's screen.", 410, session_id=sid)

            if not query_one("SELECT id FROM enrollments WHERE student_id = %s AND subject_id = %s "
                             "AND class_id = %s AND semester_id = %s AND status = 'active'",
                             (student_id, row["subject_id"], row["class_id"], row["semester_id"])):
                raise ServiceError("not_enrolled", "You are not enrolled in this subject.", 403, session_id=sid)
            if query_one("SELECT id FROM attendance_records WHERE session_id = %s AND student_id = %s",
                         (sid, student_id)):
                raise ServiceError("already_marked", "Your attendance is already recorded for this session.",
                                   409, session_id=sid)

            try:
                device_id = device_service.check_for_marking(device_hash, student_id, sid)
                location = geo_service.verify_for_session(live, body)
            except ServiceError as exc:
                exc.session_id = sid
                raise

            try:
                record_id = execute(
                    "INSERT INTO attendance_records (session_id, student_id, marked_at, distance_m, "
                    "gps_accuracy_m, device_id, ip_address) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (sid, student_id, now, location and location["distance_m"],
                     location and location["accuracy_m"], device_id, ip)).lastrowid
            except IntegrityError as exc:
                if is_duplicate_key(exc, "uq_attendance_session_student"):
                    raise ServiceError("already_marked", "Your attendance is already recorded for this "
                                       "session.", 409, session_id=sid) from None
                raise
            audit_service.log("attendance.mark", user_id=student_id, entity_type="attendance_session",
                              entity_id=sid, ip=ip)
    except ServiceError as exc:
        _log_rejection(exc, student_id, ip, None)
        raise
    return {"attendance_id": record_id, "session_id": sid, "subject": row["subject_name"],
            "marked_at": iso(now), "distance_m": location["distance_m"] if location else None}
