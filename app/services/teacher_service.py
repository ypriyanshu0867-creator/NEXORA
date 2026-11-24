"""Teacher dashboard, assigned classes, attendance lists and reports.

Every query is scoped by teacher_subjects.teacher_id = the logged-in teacher, and a
class/assignment the teacher does not hold answers 404 (not 403), so ids cannot be probed.
"""
from flask import current_app

from app import clock
from app.db import query_all, query_one
from app.services import notice_service, report_service, session_service
from app.services.errors import ServiceError

CONDUCTED = report_service._CONDUCTED            # "finished AND at least one record or correction" (one %s: now)

_ASSIGNMENT = (
    "SELECT ts.id, ts.subject_id, ts.class_id, ts.semester_id, sub.code AS subject_code, sub.name AS subject_name, "
    "       sub.credits, sub.department_id, d.name AS department, c.name AS class_name, c.section, "
    "       sem.name AS semester_name, sem.semester_number, sem.is_current, ay.label AS year_label, "
    "       (SELECT COUNT(*) FROM enrollments e WHERE e.subject_id = ts.subject_id AND e.class_id = ts.class_id "
    "          AND e.semester_id = ts.semester_id AND e.status = 'active') AS enrolled "
    "FROM teacher_subjects ts "
    "JOIN subjects sub ON sub.id = ts.subject_id JOIN departments d ON d.id = sub.department_id "
    "JOIN classes c ON c.id = ts.class_id JOIN semesters sem ON sem.id = ts.semester_id "
    "JOIN academic_years ay ON ay.id = sem.academic_year_id ")


def get_assignment(teacher_id, assignment_id):
    row = query_one(_ASSIGNMENT + "WHERE ts.id = %s AND ts.teacher_id = %s", (assignment_id, teacher_id))
    if row is None:
        raise ServiceError("not_found", "That class was not found.", 404)
    return row


def list_assignments(teacher_id):
    return query_all(_ASSIGNMENT + "WHERE ts.teacher_id = %s ORDER BY sem.is_current DESC, sem.start_date DESC, "
                     "sub.code, c.name", (teacher_id,))


# ---------------------------------------------------------------- reports
def conducted_count(assignment_id):
    return query_one("SELECT COUNT(*) AS c FROM attendance_sessions s WHERE s.assignment_id = %s AND " + CONDUCTED,
                     (assignment_id, clock.now()))["c"]


def student_report(teacher_id, assignment_id):
    """Per-student attendance for one of the teacher's classes."""
    a = get_assignment(teacher_id, assignment_id)
    now = clock.now()
    conducted = conducted_count(assignment_id)
    rows = query_all(
        "SELECT u.id, u.full_name, sp.roll_number, "
        "  (SELECT COUNT(*) FROM attendance_records ar JOIN attendance_sessions s ON s.id = ar.session_id "
        "   WHERE ar.student_id = u.id AND s.assignment_id = %s AND " + CONDUCTED + ") AS attended "
        "FROM enrollments e JOIN users u ON u.id = e.student_id "
        "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "WHERE e.subject_id = %s AND e.class_id = %s AND e.semester_id = %s AND e.status = 'active' "
        "ORDER BY sp.roll_number, u.full_name",
        (assignment_id, now, a["subject_id"], a["class_id"], a["semester_id"]))
    for r in rows:
        r["attended"] = int(r["attended"])
        r["conducted"] = conducted
        r["percent"] = report_service.percent(r["attended"], conducted)
    return {"assignment": a, "conducted": conducted, "students": rows,
            "threshold": current_app.config["ATTENDANCE_THRESHOLD_PERCENT"]}


def subject_summary(teacher_id):
    """Subject-wise (per class) averages across the teacher's assignments."""
    now = clock.now()
    out = []
    for a in list_assignments(teacher_id):
        conducted = conducted_count(a["id"])
        present = query_one(
            "SELECT COUNT(*) AS c FROM attendance_records ar JOIN attendance_sessions s ON s.id = ar.session_id "
            "WHERE s.assignment_id = %s AND " + CONDUCTED, (a["id"], now))["c"]
        possible = conducted * a["enrolled"]
        out.append({**a, "conducted": conducted, "present_total": int(present),
                    "average_percent": report_service.percent(int(present), possible)})
    return out


def student_report_csv(teacher_id, assignment_id):
    rep = student_report(teacher_id, assignment_id)
    a = rep["assignment"]
    rows = [[r["roll_number"], r["full_name"], r["attended"], r["conducted"],
             "" if r["percent"] is None else r["percent"]] for r in rep["students"]]
    name = f"attendance_{a['subject_code']}_{a['class_name']}{a['section']}.csv".replace(" ", "_")
    return name, report_service.rows_to_csv(["Roll number", "Student", "Sessions attended", "Sessions held",
                                              "Attendance %"], rows)


def subject_summary_csv(teacher_id):
    rows = [[s["subject_code"], s["subject_name"], f"{s['class_name']} {s['section']}", s["semester_name"],
             s["enrolled"], s["conducted"], "" if s["average_percent"] is None else s["average_percent"]]
            for s in subject_summary(teacher_id)]
    return "attendance_by_subject.csv", report_service.rows_to_csv(
        ["Code", "Subject", "Class", "Semester", "Enrolled", "Sessions held", "Average attendance %"], rows)


def session_list(teacher_id, assignment_id=None, semester_id=None, date_from=None, date_to=None, state="",
                 page=1, page_size=15):
    """The teacher's own sessions with filters (subject+class, semester, dates, state)."""
    now = clock.now()
    where, params = ["ts.teacher_id = %s"], [teacher_id]
    if assignment_id:
        where.append("ts.id = %s"); params.append(assignment_id)
    if semester_id:
        where.append("ts.semester_id = %s"); params.append(semester_id)
    if date_from:
        where.append("s.started_at >= %s"); params.append(date_from)
    if date_to:
        where.append("s.started_at < %s"); params.append(date_to + _day())
    if state == "active":
        where.append("s.status = 'active' AND s.session_ends_at > %s"); params.append(now)
    elif state == "closed":
        where.append("(s.status = 'closed' OR s.session_ends_at <= %s)"); params.append(now)
    clause = " AND ".join(where)
    base = ("FROM attendance_sessions s JOIN teacher_subjects ts ON ts.id = s.assignment_id "
            "JOIN subjects sub ON sub.id = ts.subject_id JOIN classes c ON c.id = ts.class_id "
            "JOIN semesters sem ON sem.id = ts.semester_id LEFT JOIN classrooms cr ON cr.id = s.classroom_id WHERE ")
    total = query_one("SELECT COUNT(*) AS c " + base + clause, tuple(params))["c"]
    pages = max(1, -(-total // page_size))
    page = min(max(page, 1), pages)
    rows = query_all(
        "SELECT s.id, s.status, s.started_at, s.session_ends_at, s.closed_at, s.geofence_enabled, s.geo_radius_m, "
        "       s.qr_validity_seconds, ts.id AS assignment_id, sub.code AS subject_code, sub.name AS subject_name, "
        "       c.name AS class_name, c.section, sem.name AS semester_name, cr.name AS classroom, "
        "       (SELECT COUNT(*) FROM attendance_records ar WHERE ar.session_id = s.id) AS present "
        + base + clause + " ORDER BY s.started_at DESC, s.id DESC LIMIT %s OFFSET %s",
        tuple(params) + (page_size, (page - 1) * page_size))
    for r in rows:
        live = r["status"] == "active" and r["session_ends_at"] > now
        r["state"] = "active" if live else ("closed" if r["status"] == "closed" else "ended")
    return {"items": rows, "total": total, "page": page, "pages": pages}


def _day():
    import datetime
    return datetime.timedelta(days=1)


# --------------------------------------------------------------- dashboard
def dashboard(teacher):
    tid, now = teacher["id"], clock.now()
    assignments = list_assignments(tid)
    summary = subject_summary(tid)
    possible = sum(s["conducted"] * s["enrolled"] for s in summary)
    present = sum(s["present_total"] for s in summary)
    sessions_conducted = sum(s["conducted"] for s in summary)
    today_start = now.replace(hour=0, minute=0, second=0)
    present_today = query_one(
        "SELECT COUNT(*) AS c FROM attendance_records ar JOIN attendance_sessions s ON s.id = ar.session_id "
        "JOIN teacher_subjects ts ON ts.id = s.assignment_id WHERE ts.teacher_id = %s AND ar.marked_at >= %s",
        (tid, today_start))["c"]
    activity = query_all(
        "SELECT ar.marked_at, ar.source, u.full_name, sub.code FROM attendance_records ar "
        "JOIN attendance_sessions s ON s.id = ar.session_id JOIN teacher_subjects ts ON ts.id = s.assignment_id "
        "JOIN subjects sub ON sub.id = ts.subject_id JOIN users u ON u.id = ar.student_id "
        "WHERE ts.teacher_id = %s ORDER BY ar.marked_at DESC LIMIT 8", (tid,))
    profile = query_one("SELECT tp.employee_code, tp.designation, d.id AS department_id, d.name AS department "
                        "FROM teacher_profiles tp JOIN departments d ON d.id = tp.department_id WHERE tp.user_id = %s", (tid,))
    viewer = {"role": "teacher", "department_id": profile["department_id"] if profile else None}
    notices = notice_service.list_notices(viewer, page_size=20)["items"]
    return {
        "profile": profile, "assignments": assignments, "summary": summary,
        "stats": {"subjects": len({a["subject_id"] for a in assignments}),
                  "classes": len({a["class_id"] for a in assignments}),
                  "sessions_conducted": sessions_conducted, "present_today": int(present_today),
                  "average_percent": report_service.percent(present, possible)},
        "active_sessions": session_service.list_active_sessions(tid),
        "recent_sessions": session_list(tid, page_size=5)["items"],
        "activity": activity, "notices": notices[:5],
        "deadlines": [n for n in notices if n["deadline_at"] and n["deadline_at"] >= now][:4],
    }
