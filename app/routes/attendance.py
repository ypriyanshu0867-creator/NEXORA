"""Attendance pages (blueprint 'attendance') and JSON API (blueprint 'attendance_api').

Page endpoints are named in app/navigation.py: attendance.scan,
attendance.history, attendance.teacher_sessions.
"""
from flask import Blueprint, Response, g, render_template, request

from app import clock
from app.api import api_error, api_ok, json_body
from app.clock import iso
from app.db import query_one
from app.security import login_required, role_required
from app.services import attendance_service, device_service, report_service, session_service
from app.services.errors import ServiceError

bp = Blueprint("attendance", __name__)
api_bp = Blueprint("attendance_api", __name__)


# ------------------------------------------------------------------ pages
@bp.route("/attendance/scan")
@role_required("student")
def scan():
    return render_template("student/scan.html")


@bp.route("/attendance/history")
@role_required("student")
def history():
    return render_template("student/attendance.html",
                           summary=report_service.student_summary(g.user["id"]),
                           history=report_service.student_history(g.user["id"]))


@bp.route("/teacher/attendance")
@role_required("teacher")
def teacher_sessions():
    return render_template("teacher/attendance.html",
                           assignments=session_service.list_assignments(g.user["id"]),
                           classrooms=session_service.list_classrooms())


# -------------------------------------------------------------------- API
def _fail(exc):
    return api_error(exc.code, exc.message, exc.status, retry_after=exc.retry_after, fields=exc.fields)


@api_bp.get("/api/teacher/subjects")
@role_required("teacher")
def api_teacher_subjects():
    return api_ok({"assignments": session_service.list_assignments(g.user["id"]),
                   "classrooms": [{**r, "latitude": float(r["latitude"]), "longitude": float(r["longitude"])}
                                  for r in session_service.list_classrooms()]})


@api_bp.post("/api/teacher/attendance-session")
@role_required("teacher")
def api_start_session():
    body = json_body()
    if body is None:
        return api_error("invalid_request", "Send a JSON object.", 400)
    try:
        cleaned = session_service.validate_start_request(body)
        return api_ok(session_service.start_session(g.user["id"], cleaned, request.remote_addr), 201)
    except ServiceError as exc:
        return _fail(exc)


@api_bp.get("/api/teacher/attendance-session/active")
@role_required("teacher")
def api_active_sessions():
    return api_ok({"sessions": session_service.list_active_sessions(g.user["id"])})


@api_bp.get("/api/teacher/attendance-session/<int:session_id>")
@role_required("teacher")
def api_session_detail(session_id):
    try:
        return api_ok(session_service.get_session(g.user["id"], session_id, include_records=True))
    except ServiceError as exc:
        return _fail(exc)


@api_bp.post("/api/teacher/attendance-session/<int:session_id>/qr")
@role_required("teacher")
def api_session_qr(session_id):
    try:
        return api_ok(session_service.issue_qr(g.user["id"], session_id))
    except ServiceError as exc:
        return _fail(exc)


@api_bp.post("/api/teacher/attendance-session/<int:session_id>/close")
@role_required("teacher")
def api_close_session(session_id):
    try:
        return api_ok(session_service.close_session(g.user["id"], session_id, request.remote_addr))
    except ServiceError as exc:
        return _fail(exc)


@api_bp.get("/api/teacher/attendance")
@role_required("teacher")
def api_teacher_attendance():
    session_id = request.args.get("session_id", type=int)
    assignment_id = request.args.get("assignment_id", type=int)
    rows = report_service.teacher_records(g.user["id"], session_id, assignment_id)
    if request.args.get("format") == "csv":
        name = f"attendance_session_{session_id}.csv" if session_id else "attendance.csv"
        return Response(report_service.to_csv(rows), mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    return api_ok({"records": [
        {"session_id": r["session_id"], "subject_code": r["subject_code"], "student": r["full_name"],
         "roll_number": r["roll_number"], "marked_at": iso(r["marked_at"]),
         "distance_m": None if r["distance_m"] is None else float(r["distance_m"])} for r in rows]})


@api_bp.post("/api/attendance/mark")
@role_required("student")
def api_mark():
    body = json_body()
    if body is None or not isinstance(body.get("qr"), str):
        return api_error("invalid_request", "Send a JSON object with the scanned code in 'qr'.", 400)
    try:
        # Only the authenticated user is ever marked; any student_id in the body is ignored.
        result = attendance_service.mark_attendance(g.user, body["qr"], body,
                                                    device_service.cookie_hash(), request.remote_addr)
    except ServiceError as exc:
        return _fail(exc)
    return api_ok(result, 201)


@api_bp.get("/api/student/attendance")
@role_required("student")
def api_student_attendance():
    return api_ok({"summary": report_service.student_summary(g.user["id"]),
                   "history": [{k: v for k, v in h.items() if k != "started_at"}
                               for h in report_service.student_history(g.user["id"])]})


@api_bp.get("/api/attendance/session/<int:session_id>")
@role_required("student")
def api_student_session(session_id):
    """Minimal view of a session the student is enrolled in."""
    row = query_one(
        "SELECT s.id, s.status, s.session_ends_at, sub.code, sub.name, ar.marked_at "
        "FROM attendance_sessions s "
        "JOIN teacher_subjects ts ON ts.id = s.assignment_id "
        "JOIN subjects sub ON sub.id = ts.subject_id "
        "JOIN enrollments e ON e.student_id = %s AND e.subject_id = ts.subject_id "
        "     AND e.class_id = ts.class_id AND e.semester_id = ts.semester_id AND e.status = 'active' "
        "LEFT JOIN attendance_records ar ON ar.session_id = s.id AND ar.student_id = %s "
        "WHERE s.id = %s", (g.user["id"], g.user["id"], session_id))
    if row is None:
        return api_error("not_found", "Session not found.", 404)
    live = row["status"] == "active" and row["session_ends_at"] > clock.now()
    return api_ok({"id": row["id"], "subject_code": row["code"], "subject_name": row["name"],
                   "accepting_attendance": live, "you_are_marked": row["marked_at"] is not None,
                   "marked_at": iso(row["marked_at"])})


@api_bp.get("/api/device/status")
@login_required
def api_device_status():
    if g.user["role"] != "student":
        return api_ok({"applicable": False})
    return api_ok({"applicable": True,
                   **device_service.status_for(device_service.cookie_hash(), g.user["id"])})
