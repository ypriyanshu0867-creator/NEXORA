"""Student dashboard: one place that gathers real figures from each module.
Nothing is hard-coded; missing data stays None/empty so templates show empty states."""
from flask import current_app

from app.db import query_all
from app.services import (academic_service, fee_service, library_service, notice_service,
                          profile_service, report_service)


def _recent_activity(student_id, notices, limit=10):
    items = []
    for r in query_all(
            "SELECT ar.marked_at, sub.code, sub.name FROM attendance_records ar "
            "JOIN attendance_sessions s ON s.id = ar.session_id "
            "JOIN teacher_subjects ts ON ts.id = s.assignment_id "
            "JOIN subjects sub ON sub.id = ts.subject_id "
            "WHERE ar.student_id = %s ORDER BY ar.marked_at DESC LIMIT 5", (student_id,)):
        items.append({"when": r["marked_at"], "kind": "Attendance", "icon": "bi-qr-code-scan",
                      "text": f"Marked present in {r['code']} · {r['name']}", "url": "/attendance/history"})
    for n in notices:
        items.append({"when": n["published"], "kind": "Notice", "icon": "bi-megaphone",
                      "text": n["title"], "url": f"/notices/{n['id']}"})
    for r in query_all(
            "SELECT r.entered_at, r.grade_label, sub.code FROM academic_results r "
            "JOIN subjects sub ON sub.id = r.subject_id "
            "WHERE r.student_id = %s AND r.is_published = 1 ORDER BY r.entered_at DESC LIMIT 5", (student_id,)):
        items.append({"when": r["entered_at"], "kind": "Result", "icon": "bi-mortarboard",
                      "text": f"Result recorded for {r['code']} (grade {r['grade_label']})",
                      "url": "/academics/results"})
    items.sort(key=lambda i: i["when"], reverse=True)
    return items[:limit]


def dashboard(user):
    uid = user["id"]
    ctx = profile_service.get_context(user)
    viewer = {"role": "student", "department_id": ctx["department_id"],
              "semester_number": ctx["semester_number"], "class_id": ctx.get("class_id")}
    threshold = current_app.config["ATTENDANCE_THRESHOLD_PERCENT"]

    attendance = report_service.student_summary(uid)
    attention = [s for s in attendance["subjects"] if s["percent"] is not None and s["percent"] < threshold]

    cgpa = academic_service.official_cgpa(uid)
    latest_sem = cgpa["semesters"][-1] if cgpa["semesters"] else None
    fees = fee_service.fee_overview(uid)
    library = library_service.student_issues(uid)
    recent_notices = notice_service.list_notices(viewer, page_size=5)["items"]
    exams = notice_service.list_notices(viewer, category="exam", page_size=3)["items"]
    latest_results = query_all(
        "SELECT r.grade_label, r.grade_point, r.entered_at, sub.code, sub.name, sem.name AS semester_name "
        "FROM academic_results r JOIN subjects sub ON sub.id = r.subject_id "
        "JOIN semesters sem ON sem.id = r.semester_id "
        "WHERE r.student_id = %s AND r.is_published = 1 ORDER BY r.entered_at DESC, r.id DESC LIMIT 5", (uid,))

    return {
        "profile": profile_service.get_profile_details(uid),
        "attendance": attendance, "attention": attention, "threshold": threshold,
        "latest_sgpa": latest_sem, "cgpa": cgpa if cgpa["cgpa"] is not None else None,
        "latest_results": latest_results, "exam_notices": exams,
        "fees": fees, "library": library, "notices": recent_notices,
        "activity": _recent_activity(uid, recent_notices),
    }
