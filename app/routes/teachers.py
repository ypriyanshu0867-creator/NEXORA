"""Teacher dashboard and management pages. Every route is teacher-only and every record is
looked up through the logged-in teacher's own assignments (unknown or foreign ids answer 404)."""
from flask import Blueprint, Response, flash, g, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from app.routes._helpers import date_arg, page_arg
from app.security import role_required
from app.services import (content_service, correction_service, result_service, teacher_notice_service,
                          teacher_service)
from app.services.errors import ServiceError

bp = Blueprint("teacher", __name__, url_prefix="/teacher")


def _csv(name, text):
    return Response(text, mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{secure_filename(name) or "export.csv"}"'})


def _flash_error(exc):
    detail = "; ".join(f"{v}" for v in (exc.fields or {}).values())
    flash(exc.message + (f" ({detail})" if detail else ""), "danger")


# ----------------------------------------------------------- dashboard / classes
@bp.route("/")
@role_required("teacher")
def dashboard():
    return render_template("teacher/dashboard.html", d=teacher_service.dashboard(g.user))


@bp.route("/classes")
@role_required("teacher")
def classes():
    return render_template("teacher/classes.html", assignments=teacher_service.list_assignments(g.user["id"]))


@bp.route("/classes/<int:assignment_id>")
@role_required("teacher")
def class_detail(assignment_id):
    report = teacher_service.student_report(g.user["id"], assignment_id)
    recent = teacher_service.session_list(g.user["id"], assignment_id=assignment_id, page_size=5)["items"]
    return render_template("teacher/class_detail.html", report=report, recent=recent)


# ------------------------------------------------------------------ attendance
@bp.route("/sessions")
@role_required("teacher")
def sessions():
    result = teacher_service.session_list(
        g.user["id"], request.args.get("assignment_id", type=int), request.args.get("semester_id", type=int),
        date_arg("from"), date_arg("to"), request.args.get("state", ""), page_arg())
    return render_template("teacher/sessions.html", result=result, assignments=teacher_service.list_assignments(g.user["id"]),
                           f={k: request.args.get(k, "") for k in ("assignment_id", "semester_id", "from", "to", "state")})


@bp.route("/sessions/<int:session_id>")
@role_required("teacher")
def session_detail(session_id):
    return render_template("teacher/session_detail.html", r=correction_service.roster(g.user["id"], session_id))


@bp.route("/sessions/<int:session_id>/correct", methods=["POST"])
@role_required("teacher")
def correct(session_id):
    try:
        correction_service.apply_correction(
            g.user["id"], session_id, request.form.get("student_id", type=int),
            {"1": True, "0": False}.get(request.form.get("present")), request.form.get("reason", ""), request.remote_addr)
        flash("Attendance corrected and recorded in the audit trail.", "success")
    except ServiceError as exc:
        if exc.status == 404:
            response_status = exc.status
            raise
        _flash_error(exc)
    return redirect(url_for("teacher.session_detail", session_id=session_id))


@bp.route("/reports")
@role_required("teacher")
def reports():
    tid = g.user["id"]
    assignment_id = request.args.get("assignment_id", type=int)
    if request.args.get("format") == "csv":
        name, text = (teacher_service.student_report_csv(tid, assignment_id) if assignment_id
                      else teacher_service.subject_summary_csv(tid))
        return _csv(name, text)
    return render_template("teacher/reports.html", summary=teacher_service.subject_summary(tid),
                           report=teacher_service.student_report(tid, assignment_id) if assignment_id else None)


# --------------------------------------------------------------------- results
@bp.route("/results")
@role_required("teacher")
def results():
    return render_template("teacher/results.html", assignments=teacher_service.list_assignments(g.user["id"]))


@bp.route("/results/<int:assignment_id>", methods=["GET", "POST"])
@role_required("teacher")
def results_entry(assignment_id):
    tid = g.user["id"]
    errors, submitted = {}, {}
    response_status = 200

    if request.method == "POST":
        entries = []
        for key, value in request.form.items():
            if key.startswith("marks_") and key[6:].isdigit():
                sid = int(key[6:])
                submitted[sid] = {
                    "marks": value,
                    "max": request.form.get(f"max_{sid}", "")
                }
                entries.append({
                    "student_id": sid,
                    "marks": value,
                    "max_marks": request.form.get(f"max_{sid}", "")
                })

        try:
            done = result_service.save_results(
                tid,
                assignment_id,
                entries,
                request.form.get("action") == "publish",
                request.remote_addr
            )
            flash(
                f"Saved {done['saved']} result(s)"
                + (f"; published {done['published']}." if done["published"] else "."),
                "success"
            )
            return redirect(url_for("teacher.results_entry", assignment_id=assignment_id))

        except ServiceError as exc:
            if exc.status == 404:
                raise

            response_status = exc.status
            errors = exc.fields or {}
            flash(exc.message, "danger")

    return render_template(
        "teacher/results_entry.html",
        grid=result_service.get_grid(tid, assignment_id),
        errors=errors,
        submitted=submitted
    ), (response_status if response_status != 200 else (422 if errors else 200))


@bp.route("/results/<int:assignment_id>/export")
@role_required("teacher")
def results_export(assignment_id):
    return _csv(*result_service.export_csv(g.user["id"], assignment_id))


# ------------------------------------------------------- syllabus and materials
@bp.route("/syllabus")
@role_required("teacher")
def syllabus():
    return render_template("teacher/syllabus.html", subjects=content_service.assigned_subjects(g.user["id"]))


@bp.route("/syllabus/<int:subject_id>")
@role_required("teacher")
def syllabus_subject(subject_id):
    edit_id = request.args.get("edit", type=int)
    data = content_service.overview(g.user["id"], subject_id)
    editing = next((u for u in data["units"] if u["id"] == edit_id), None)
    return render_template("teacher/syllabus_subject.html", d=data, editing=editing, errors={})


@bp.route("/syllabus/<int:subject_id>/units", methods=["POST"])
@role_required("teacher")
def save_unit(subject_id):
    unit_id = request.form.get("unit_id", type=int)
    try:
        content_service.save_unit(g.user["id"], subject_id, {**request.form, "is_published": request.form.get("is_published") == "1"},
                                  unit_id, request.remote_addr)
        flash("Unit saved.", "success")
    except ServiceError as exc:
        if exc.status == 404:
            raise
        data = content_service.overview(g.user["id"], subject_id)
        flash(exc.message, "danger")
        return render_template("teacher/syllabus_subject.html", d=data, editing={**request.form, "id": unit_id},
                               errors=exc.fields or {}), 422
    return redirect(url_for("teacher.syllabus_subject", subject_id=subject_id))


@bp.route("/syllabus/units/<int:unit_id>/publish", methods=["POST"])
@role_required("teacher")
def unit_publish(unit_id):
    subject_id = content_service.set_unit_published(g.user["id"], unit_id, request.form.get("publish") == "1", request.remote_addr)
    flash("Unit publication updated.", "success")
    return redirect(url_for("teacher.syllabus_subject", subject_id=subject_id))


@bp.route("/syllabus/<int:subject_id>/materials", methods=["POST"])
@role_required("teacher")
def upload_material(subject_id):
    try:
        content_service.add_material(g.user["id"], subject_id, request.form.get("title"), request.form.get("description"),
                                     request.files.get("file"), request.form.get("publish") == "1", request.remote_addr)
        flash("Material uploaded.", "success")
    except ServiceError as exc:
        if exc.status == 404:
            raise
        _flash_error(exc)
    return redirect(url_for("teacher.syllabus_subject", subject_id=subject_id))


@bp.route("/syllabus/materials/<int:material_id>/update", methods=["POST"])
@role_required("teacher")
def update_material(material_id):
    try:
        subject_id = content_service.update_material(g.user["id"], material_id, request.form.get("title"),
                                                     request.form.get("description"), request.remote_addr)
    except ServiceError as exc:
        if exc.status == 404:
            raise
        _flash_error(exc)
        return redirect(request.referrer or url_for("teacher.syllabus"))
    flash("Material updated.", "success")
    return redirect(url_for("teacher.syllabus_subject", subject_id=subject_id))


@bp.route("/syllabus/materials/<int:material_id>/publish", methods=["POST"])
@role_required("teacher")
def material_publish(material_id):
    subject_id = content_service.set_material_published(g.user["id"], material_id, request.form.get("publish") == "1",
                                                        request.remote_addr)
    flash("Material publication updated.", "success")
    return redirect(url_for("teacher.syllabus_subject", subject_id=subject_id))


# --------------------------------------------------------------------- notices
@bp.route("/notices")
@role_required("teacher")
def notices():
    return render_template("teacher/notices.html", own=teacher_notice_service.list_own(g.user["id"]))


@bp.route("/notices/new", methods=["GET", "POST"])
@bp.route("/notices/<int:notice_id>/edit", methods=["GET", "POST"])
@role_required("teacher")
def notice_form(notice_id=None):
    tid = g.user["id"]
    scope = teacher_notice_service.scope_options(tid)
    existing = teacher_notice_service.get_own(tid, notice_id) if notice_id else None
    errors, values = {}, existing or {}
    if request.method == "POST":
        action = request.form.get("action")
        try:
            saved = teacher_notice_service.save_notice(
                tid, request.form, notice_id, request.files.get("attachment"),
                {"publish": True, "draft": False}.get(action), request.remote_addr)
            flash("Notice published." if action == "publish" else "Notice saved.", "success")
            return redirect(url_for("teacher.notices"))
        except ServiceError as exc:
            if exc.status == 404:
                raise
            errors, values = exc.fields or {}, {**(existing or {}), **request.form.to_dict()}
            flash(exc.message, "danger")
    return render_template("teacher/notice_form.html", scope=scope, categories=teacher_notice_service.LABELS,
                           errors=errors, v=values, notice_id=notice_id, existing=existing), (422 if errors else 200)


@bp.route("/notices/<int:notice_id>/publish", methods=["POST"])
@role_required("teacher")
def notice_publish(notice_id):
    teacher_notice_service.set_published(g.user["id"], notice_id, request.form.get("publish") == "1", request.remote_addr)
    flash("Notice published." if request.form.get("publish") == "1" else "Notice withdrawn (students no longer see it).", "success")
    return redirect(url_for("teacher.notices"))
