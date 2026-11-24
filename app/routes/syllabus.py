from flask import Blueprint, abort, g, render_template, request

from app.db import query_all
from app.routes._helpers import context
from app.security import login_required
from app.services import file_service, syllabus_service

bp = Blueprint("syllabus", __name__, url_prefix="/syllabus")


def _scope():
    """Department/semester scope for the viewer. Admins choose a department."""
    ctx = context()
    departments = []
    if g.user["role"] == "admin":
        departments = query_all("SELECT id, name FROM departments ORDER BY name")
        wanted = request.args.get("department", type=int)
        ctx["department_id"] = wanted if wanted in {d["id"] for d in departments} else (
            departments[0]["id"] if departments else None)
    return ctx, departments


@bp.route("/")
@login_required
def index():
    ctx, departments = _scope()
    semester = request.args.get("semester", type=int) or ctx["semester_number"] or 1
    if not 1 <= semester <= 8:
        abort(404)
    view = syllabus_service.semester_view(ctx, semester, g.user["role"]) if ctx["department_id"] else None
    return render_template("student/syllabus.html", view=view, semester=semester,
                           semesters=syllabus_service.semester_numbers(ctx), departments=departments,
                           department_id=ctx["department_id"], current_semester=ctx["semester_number"])


@bp.route("/materials/<int:material_id>/download")
@login_required
def download(material_id):
    ctx, _ = _scope()
    row = syllabus_service.material_for_download(ctx, g.user["role"], material_id)
    if row is None:
        abort(404)                                       # same answer for "missing" and "not yours"
    return file_service.send_pdf("materials", row["stored_filename"], row["original_filename"],
                                 f"material-{row['id']}.pdf")
