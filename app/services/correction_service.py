"""Audited attendance corrections.

A teacher may correct a student's attendance for a FINISHED session of one of their own
classes, with a written reason. The change is made by adding or removing the attendance record
(so every report and percentage stays authoritative), while attendance_corrections keeps the
original values, who changed them, when and why. That table is append-only (application code only
inserts; install the optional DB triggers with scripts\\protect_audit_tables.py to enforce it)
and every correction also writes an audit_logs entry. Nothing is silently changed.
"""
from app import clock
from app.clock import iso
from app.db import execute, query_all, query_one, transaction
from app.services import audit_service
from app.services.errors import ServiceError

REASON_MIN, REASON_MAX = 5, 255


def roster(teacher_id, session_id):
    """Everyone enrolled in the class with their current state, plus the correction history."""
    sess = query_one(
        "SELECT s.id, s.status, s.started_at, s.session_ends_at, ts.id AS assignment_id, ts.subject_id, ts.class_id, "
        "       ts.semester_id, sub.code AS subject_code, sub.name AS subject_name, c.name AS class_name, c.section "
        "FROM attendance_sessions s JOIN teacher_subjects ts ON ts.id = s.assignment_id "
        "JOIN subjects sub ON sub.id = ts.subject_id JOIN classes c ON c.id = ts.class_id "
        "WHERE s.id = %s AND ts.teacher_id = %s", (session_id, teacher_id))
    if sess is None:
        raise ServiceError("not_found", "Session not found.", 404)
    sess["finished"] = sess["status"] == "closed" or sess["session_ends_at"] <= clock.now()
    students = query_all(
        "SELECT u.id, u.full_name, sp.roll_number, ar.marked_at, ar.source, (ar.id IS NOT NULL) AS present "
        "FROM enrollments e JOIN users u ON u.id = e.student_id LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "LEFT JOIN attendance_records ar ON ar.session_id = %s AND ar.student_id = u.id "
        "WHERE e.subject_id = %s AND e.class_id = %s AND e.semester_id = %s AND e.status = 'active' "
        "ORDER BY sp.roll_number, u.full_name", (session_id, sess["subject_id"], sess["class_id"], sess["semester_id"]))
    history = query_all(
        "SELECT c.corrected_at, c.previous_present, c.new_present, c.reason, st.full_name AS student, "
        "       sp.roll_number, t.full_name AS teacher "
        "FROM attendance_corrections c JOIN users st ON st.id = c.student_id JOIN users t ON t.id = c.corrected_by "
        "LEFT JOIN student_profiles sp ON sp.user_id = c.student_id "
        "WHERE c.session_id = %s ORDER BY c.id DESC", (session_id,))
    return {"session": sess, "students": students, "history": history}


def apply_correction(teacher_id, session_id, student_id, present, reason, ip):
    reason = " ".join((reason or "").split())
    errors = {}
    if not isinstance(present, bool):
        errors["present"] = "Choose present or absent."
    if not REASON_MIN <= len(reason) <= REASON_MAX:
        errors["reason"] = f"Give a reason ({REASON_MIN} to {REASON_MAX} characters)."
    if errors:
        raise ServiceError("validation_error", "Please correct the highlighted fields.", 422, fields=errors)
    now = clock.now()
    with transaction():
        sess = query_one(
            "SELECT s.id, s.status, s.session_ends_at, ts.subject_id, ts.class_id, ts.semester_id "
            "FROM attendance_sessions s JOIN teacher_subjects ts ON ts.id = s.assignment_id "
            "WHERE s.id = %s AND ts.teacher_id = %s FOR UPDATE OF s", (session_id, teacher_id))
        if sess is None:
            raise ServiceError("not_found", "Session not found.", 404)
        if sess["status"] == "active" and sess["session_ends_at"] > now:
            raise ServiceError("session_still_open", "Close the session before correcting attendance.", 409)
        if not query_one("SELECT id FROM enrollments WHERE student_id = %s AND subject_id = %s AND class_id = %s "
                         "AND semester_id = %s AND status = 'active'",
                         (student_id, sess["subject_id"], sess["class_id"], sess["semester_id"])):
            raise ServiceError("not_found", "That student is not in this class.", 404)
        record = query_one("SELECT id, marked_at, distance_m, gps_accuracy_m, device_id, source "
                           "FROM attendance_records WHERE session_id = %s AND student_id = %s FOR UPDATE",
                           (session_id, student_id))
        if (record is not None) == present:
            raise ServiceError("no_change", "That is already the recorded attendance.", 409)
        old = record or {}
        execute("INSERT INTO attendance_corrections (session_id, student_id, previous_present, new_present, "
                "old_marked_at, old_distance_m, old_gps_accuracy_m, old_device_id, old_source, reason, corrected_by, "
                "corrected_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (session_id, student_id, 1 if record else 0, 1 if present else 0, old.get("marked_at"),
                 old.get("distance_m"), old.get("gps_accuracy_m"), old.get("device_id"), old.get("source"),
                 reason, teacher_id, now))
        if present:
            execute("INSERT INTO attendance_records (session_id, student_id, marked_at, source) "
                    "VALUES (%s, %s, %s, 'correction')", (session_id, student_id, now))
        else:
            execute("DELETE FROM attendance_records WHERE id = %s", (record["id"],))
        audit_service.log("attendance.correct", user_id=teacher_id, entity_type="attendance",
                          entity_id=session_id, ip=ip,
                          details={"student_id": student_id, "from": "present" if record else "absent",
                                   "to": "present" if present else "absent", "reason": reason})
    return {"session_id": session_id, "student_id": student_id, "present": present, "corrected_at": iso(now)}
