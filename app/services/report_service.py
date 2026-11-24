"""Attendance reports for students and teachers.

DEFINITIONS (documented policy)
* A session is CONDUCTED when it has finished (closed, or past its end time)
  AND at least one student was marked (or a teacher correction was recorded for it). Finished sessions with nobody marked
  are treated as cancelled/never held and count for nobody.
* Attendance % = sessions attended / sessions conducted, per subject; overall
  is total attended / total conducted. With nothing conducted the value is None.
* Excused absences are not supported by the Phase 1 schema, so every
  conducted session the student missed counts as absent.
"""
import csv
import io

from app import clock
from app.clock import iso
from app.db import query_all

_CONDUCTED = ("(s.status = 'closed' OR s.session_ends_at <= %s) "
              "AND (EXISTS (SELECT 1 FROM attendance_records x WHERE x.session_id = s.id) "
              "OR EXISTS (SELECT 1 FROM attendance_corrections c WHERE c.session_id = s.id))")


def percent(attended, conducted):
    return None if not conducted else round(100.0 * attended / conducted, 1)


def student_summary(student_id):
    now = clock.now()
    rows = query_all(
        "SELECT sub.id AS subject_id, sub.code, sub.name, "
        "       COUNT(DISTINCT s.id) AS conducted, COUNT(DISTINCT ar.id) AS attended "
        "FROM enrollments e "
        "JOIN subjects sub ON sub.id = e.subject_id "
        "LEFT JOIN teacher_subjects ts ON ts.subject_id = e.subject_id AND ts.class_id = e.class_id "
        "     AND ts.semester_id = e.semester_id "
        "LEFT JOIN attendance_sessions s ON s.assignment_id = ts.id AND " + _CONDUCTED + " "
        "LEFT JOIN attendance_records ar ON ar.session_id = s.id AND ar.student_id = e.student_id "
        "WHERE e.student_id = %s AND e.status = 'active' "
        "GROUP BY sub.id, sub.code, sub.name ORDER BY sub.code", (now, student_id))
    subjects = [{"subject_id": r["subject_id"], "code": r["code"], "name": r["name"],
                 "conducted": int(r["conducted"]), "attended": int(r["attended"]),
                 "percent": percent(int(r["attended"]), int(r["conducted"]))} for r in rows]
    conducted = sum(s["conducted"] for s in subjects)
    attended = sum(s["attended"] for s in subjects)
    return {"overall_percent": percent(attended, conducted), "attended": attended,
            "conducted": conducted, "subjects": subjects}


def student_history(student_id, limit=50):
    now = clock.now()
    rows = query_all(
        "SELECT s.id AS session_id, s.started_at, sub.code, sub.name, ar.marked_at, "
        "       (ar.id IS NOT NULL) AS present "
        "FROM enrollments e "
        "JOIN subjects sub ON sub.id = e.subject_id "
        "JOIN teacher_subjects ts ON ts.subject_id = e.subject_id AND ts.class_id = e.class_id "
        "     AND ts.semester_id = e.semester_id "
        "JOIN attendance_sessions s ON s.assignment_id = ts.id AND " + _CONDUCTED + " "
        "LEFT JOIN attendance_records ar ON ar.session_id = s.id AND ar.student_id = e.student_id "
        "WHERE e.student_id = %s AND e.status = 'active' "
        "ORDER BY s.started_at DESC LIMIT %s", (now, student_id, int(limit)))
    return [{"session_id": r["session_id"], "date": iso(r["started_at"]), "started_at": r["started_at"],
             "subject_code": r["code"], "subject_name": r["name"],
             "status": "Present" if r["present"] else "Absent", "marked_at": iso(r["marked_at"])}
            for r in rows]


def teacher_records(teacher_id, session_id=None, assignment_id=None, limit=5000):
    """Records for sessions that belong to this teacher's assignments only."""
    sql = ("SELECT s.id AS session_id, s.started_at, sub.code AS subject_code, sub.name AS subject_name, "
           "       c.name AS class_name, c.section, u.full_name, sp.roll_number, ar.marked_at, "
           "       ar.distance_m, ar.gps_accuracy_m "
           "FROM attendance_records ar "
           "JOIN attendance_sessions s ON s.id = ar.session_id "
           "JOIN teacher_subjects ts ON ts.id = s.assignment_id "
           "JOIN subjects sub ON sub.id = ts.subject_id "
           "JOIN classes c ON c.id = ts.class_id "
           "JOIN users u ON u.id = ar.student_id "
           "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
           "WHERE ts.teacher_id = %s")
    params = [teacher_id]
    if session_id is not None:
        sql += " AND s.id = %s"
        params.append(session_id)
    if assignment_id is not None:
        sql += " AND ts.id = %s"
        params.append(assignment_id)
    sql += " ORDER BY s.started_at DESC, u.full_name LIMIT %s"
    params.append(int(limit))
    return query_all(sql, tuple(params))


def _safe_cell(value):
    """Stop spreadsheet formula injection: prefix risky leading characters."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


CSV_HEADER = ["Session ID", "Session start (UTC)", "Subject code", "Subject", "Class", "Student",
              "Roll number", "Marked at (UTC)", "Distance (m)", "GPS accuracy (m)"]


def to_csv(rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_HEADER)
    for r in rows:
        writer.writerow([_safe_cell(v) for v in (
            r["session_id"], r["started_at"], r["subject_code"], r["subject_name"],
            f"{r['class_name']} {r['section']}", r["full_name"], r["roll_number"], r["marked_at"],
            r["distance_m"], r["gps_accuracy_m"])])
    return out.getvalue()


def rows_to_csv(header, rows):
    """Generic CSV with spreadsheet-formula neutralisation (header + list of row lists)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_safe_cell(v) for v in row])
    return out.getvalue()
