import math
from app.db import IntegrityError
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.security import role_required
from app.services import admin_service

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@role_required("admin")
def dashboard():
    return render_template("admin/dashboard.html", counts=admin_service.dashboard_counts())


@bp.route("/approvals")
@role_required("admin")
def pending_users():
    return render_template("admin/pending_users.html", pending=admin_service.list_pending_users())


def _review(user_id, new_status, done_message):
    if admin_service.review_pending_user(g.user["id"], user_id, new_status, request.remote_addr):
        flash(done_message, "success")
    else:
        flash("That account was not found or has already been reviewed.", "warning")
    return redirect(url_for("admin.pending_users"))


@bp.route("/approvals/<int:user_id>/approve", methods=["POST"])
@role_required("admin")
def approve_user(user_id):
    return _review(user_id, "active", "Account approved. The user can now sign in.")


@bp.route("/approvals/<int:user_id>/reject", methods=["POST"])
@role_required("admin")
def reject_user(user_id):
    return _review(user_id, "rejected", "Registration rejected.")
@bp.route("/users")
@role_required("admin")
def users():
    search = request.args.get("search", "").strip()[:100]
    role = request.args.get("role", "").strip()
    status = request.args.get("status", "").strip()

    users_list = admin_service.list_users(search, role, status)

    return render_template(
        "admin/users.html",
        users=users_list,
        search=search,
        selected_role=role,
        selected_status=status
    )


@bp.route("/users/<int:user_id>/status", methods=["POST"])
@role_required("admin")
def update_user_status(user_id):
    new_status = request.form.get("status", "")

    try:
        changed = admin_service.update_user_status(
            g.user["id"],
            user_id,
            new_status,
            request.remote_addr
        )
    except ValueError:
        flash("Invalid account status.", "danger")
        return redirect(url_for("admin.users"))

    if changed:
        flash("User account status updated successfully.", "success")
    else:
        flash("Account could not be updated. Check its status or permissions.", "warning")

    return redirect(url_for("admin.users"))

def _departments_page():
    return render_template(
        "admin/departments.html",
        departments=admin_service.list_departments(),
        academic_years=admin_service.list_academic_years(),
        classes=admin_service.list_classes()
    )


@bp.route("/departments", methods=["GET", "POST"])
@role_required("admin")
def departments():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        name = request.form.get("name", "").strip()

        if not code or not name or len(code) > 10 or len(name) > 120:
            flash("Enter a valid department code and name.", "danger")
            return redirect(url_for("admin.departments"))

        try:
            admin_service.create_department(
                code, name, g.user["id"], request.remote_addr
            )
            flash("Department added successfully.", "success")
        except IntegrityError:
            flash("Department code or name already exists.", "warning")

        return redirect(url_for("admin.departments"))

    return _departments_page()


@bp.route("/departments/<int:department_id>/edit", methods=["POST"])
@role_required("admin")
def edit_department(department_id):
    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()

    if not code or not name or len(code) > 10 or len(name) > 120:
        flash("Enter a valid department code and name.", "danger")
        return redirect(url_for("admin.departments"))

    try:
        changed = admin_service.update_department(
            department_id, code, name, g.user["id"], request.remote_addr
        )
        flash(
            "Department updated successfully." if changed
            else "Department not found.",
            "success" if changed else "warning"
        )
    except IntegrityError:
        flash("Department code or name already exists.", "warning")

    return redirect(url_for("admin.departments"))


@bp.route("/classes", methods=["POST"])
@role_required("admin")
def add_class():
    try:
        department_id = int(request.form.get("department_id", ""))
        academic_year_id = int(request.form.get("academic_year_id", ""))
    except (TypeError, ValueError):
        flash("Select a valid department and academic year.", "danger")
        return redirect(url_for("admin.departments"))

    name = request.form.get("name", "").strip()
    section = request.form.get("section", "A").strip().upper()

    if not name or len(name) > 60 or not section or len(section) > 10:
        flash("Enter a valid class name and section.", "danger")
        return redirect(url_for("admin.departments"))

    try:
        admin_service.create_class(
            department_id, academic_year_id, name, section,
            g.user["id"], request.remote_addr
        )
        flash("Class added successfully.", "success")
    except IntegrityError:
        flash("Invalid department/year or this class already exists.", "warning")

    return redirect(url_for("admin.departments"))


@bp.route("/classes/<int:class_id>/edit", methods=["POST"])
@role_required("admin")
def edit_class(class_id):
    try:
        department_id = int(request.form.get("department_id", ""))
        academic_year_id = int(request.form.get("academic_year_id", ""))
    except (TypeError, ValueError):
        flash("Select a valid department and academic year.", "danger")
        return redirect(url_for("admin.departments"))

    name = request.form.get("name", "").strip()
    section = request.form.get("section", "A").strip().upper()

    if not name or len(name) > 60 or not section or len(section) > 10:
        flash("Enter a valid class name and section.", "danger")
        return redirect(url_for("admin.departments"))

    try:
        changed = admin_service.update_class(
            class_id, department_id, academic_year_id, name, section,
            g.user["id"], request.remote_addr
        )
        flash(
            "Class updated successfully." if changed else "Class not found.",
            "success" if changed else "warning"
        )
    except IntegrityError:
        flash("Invalid department/year or this class already exists.", "warning")

    return redirect(url_for("admin.departments"))


def _classroom_form_values():
    name = request.form.get("name", "").strip()
    building = request.form.get("building", "").strip()

    try:
        latitude = float(request.form.get("latitude", ""))
        longitude = float(request.form.get("longitude", ""))
        radius = int(request.form.get("default_radius_m", "50"))
    except (TypeError, ValueError):
        raise ValueError("Enter valid latitude, longitude and radius.")

    if not name or len(name) > 60:
        raise ValueError("Classroom name is required (maximum 60 characters).")

    if len(building) > 80:
        raise ValueError("Building name must not exceed 80 characters.")

    if not math.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90.")

    if not math.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180.")

    if not 5 <= radius <= 5000:
        raise ValueError("Radius must be between 5 and 5000 metres.")

    return name, building, latitude, longitude, radius


@bp.route("/classrooms", methods=["GET", "POST"])
@role_required("admin")
def classrooms():
    if request.method == "POST":
        try:
            values = _classroom_form_values()
            admin_service.create_classroom(
                *values, g.user["id"], request.remote_addr
            )
            flash("Classroom added successfully.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        except IntegrityError:
            flash("A classroom with this name already exists.", "warning")

        return redirect(url_for("admin.classrooms"))

    return render_template(
        "admin/classrooms.html",
        classrooms=admin_service.list_admin_classrooms()
    )


@bp.route("/classrooms/<int:classroom_id>/edit", methods=["POST"])
@role_required("admin")
def edit_classroom(classroom_id):
    try:
        values = _classroom_form_values()
        changed = admin_service.update_classroom(
            classroom_id, *values, g.user["id"], request.remote_addr
        )

        flash(
            "Classroom updated successfully." if changed
            else "Classroom not found.",
            "success" if changed else "warning"
        )
    except ValueError as exc:
        flash(str(exc), "danger")
    except IntegrityError:
        flash("A classroom with this name already exists.", "warning")

    return redirect(url_for("admin.classrooms"))

@bp.route("/audit-logs")
@role_required("admin")
def audit_logs():
    from app.services import admin_audit_service
    from app.routes._helpers import page_arg, date_arg

    action = request.args.get("action", "").strip()[:60]
    entity_type = request.args.get("entity_type", "").strip()[:40]
    search = request.args.get("search", "").strip()[:100]

    date_from_value = request.args.get("date_from", "").strip()
    date_to_value = request.args.get("date_to", "").strip()

    date_from = date_arg("date_from")
    date_to = date_arg("date_to")

    if date_from:
        date_from = date_from.date()

    if date_to:
        date_to = date_to.date()

    result = admin_audit_service.list_audit_logs(
        action=action,
        entity_type=entity_type,
        search=search,
        date_from=date_from,
        date_to=date_to,
        page=page_arg()
    )

    return render_template(
        "admin/audit_logs.html",
        **result,
        selected_action=action,
        selected_entity_type=entity_type,
        search=search,
        date_from=date_from_value if date_from else "",
        date_to=date_to_value if date_to else ""
    )
