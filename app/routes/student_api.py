"""JSON API for the student portal. Every route derives the student from the login;
no endpoint accepts a student id."""
from flask import Blueprint, g, request

from app.api import api_error, api_ok, json_body
from app.routes._helpers import context, date_arg, page_arg, viewer
from app.security import role_required
from app.services import (academic_service, fee_service, library_service, notice_service, portal_service,
                          profile_service, syllabus_service)

api_bp = Blueprint("student_api", __name__, url_prefix="/api/student")


@api_bp.get("/dashboard")
@role_required("student")
def dashboard():
    return api_ok(portal_service.dashboard(g.user))


@api_bp.get("/profile")
@role_required("student")
def profile():
    return api_ok(profile_service.get_profile_details(g.user["id"]))


@api_bp.get("/results")
@role_required("student")
def results():
    return api_ok(academic_service.results_overview(g.user["id"], context()["semester_id"]))


@api_bp.get("/academic-history")
@role_required("student")
def academic_history():
    cgpa = academic_service.official_cgpa(g.user["id"])
    return api_ok({"semesters": cgpa["semesters"], "cgpa": cgpa["cgpa"], "total_credits": cgpa["total_credits"],
                   "policy": cgpa["policy"], "label": cgpa["label"]})


@api_bp.get("/library")
@role_required("student")
def library_summary():
    issues = library_service.student_issues(g.user["id"])
    return api_ok({"issued_count": len(issues["current"]), "overdue_count": issues["overdue_count"],
                   "fines_configured": issues["fines_configured"]})


@api_bp.get("/library/books")
@role_required("student")
def library_books():
    return api_ok(library_service.search_books(request.args.get("q", ""), request.args.get("category", ""),
                                               request.args.get("available") == "1", page_arg()))


@api_bp.get("/library/issues")
@role_required("student")
def library_issues():
    return api_ok(library_service.student_issues(g.user["id"]))


@api_bp.get("/syllabus")
@role_required("student")
def syllabus():
    ctx = context()
    semester = request.args.get("semester", type=int) or ctx["semester_number"] or 1
    if not 1 <= semester <= 8:
        return api_error("validation_error", "Semester must be 1 to 8.", 422)
    return api_ok(syllabus_service.semester_view(ctx, semester, "student"))


@api_bp.get("/notices")
@role_required("student")
def notices():
    return api_ok(notice_service.list_notices(
        viewer(), request.args.get("q", ""), request.args.get("category", ""), date_arg("from"), date_arg("to"),
        request.args.get("include_expired") == "1", page_arg()))


@api_bp.get("/fees")
@role_required("student")
def fees():
    return api_ok(fee_service.fee_overview(g.user["id"]))


@api_bp.get("/calculator-data")
@role_required("student")
def calculator_data():
    return api_ok(academic_service.calculator_data(context()))


def _calc(fn):
    body = json_body()
    if body is None:
        return api_error("invalid_request", "Send a JSON object.", 400)
    return api_ok(fn(context(), body))


@api_bp.post("/sgpa/calculate")
@role_required("student")
def sgpa_calculate():
    return _calc(academic_service.calculate_sgpa)


@api_bp.post("/cgpa/calculate")
@role_required("student")
def cgpa_calculate():
    return _calc(academic_service.calculate_cgpa)
