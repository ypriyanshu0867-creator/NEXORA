from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from app.security import role_required
from app.services import auth_service, portal_service, profile_service
from app.services.errors import ServiceError

bp = Blueprint("student", __name__, url_prefix="/student")


@bp.route("/")
@role_required("student")
def dashboard():
    return render_template("student/dashboard.html", d=portal_service.dashboard(g.user))


@bp.route("/profile", methods=["GET"])
@role_required("student")
def profile():
    return render_template("student/profile.html", profile=profile_service.get_profile_details(g.user["id"]),
                           phone_error=None, pw_errors={})


@bp.route("/profile/contact", methods=["POST"])
@role_required("student")
def update_contact():
    """Only 'phone' is read from the form; every other field in the request is ignored."""
    try:
        profile_service.update_phone(g.user["id"], request.form.get("phone", ""), request.remote_addr)
    except ServiceError as exc:
        return render_template("student/profile.html", profile=profile_service.get_profile_details(g.user["id"]),
                               phone_error=exc.message, pw_errors={}), 422
    flash("Contact details updated.", "success")
    return redirect(url_for("student.profile"))


@bp.route("/profile/password", methods=["POST"])
@role_required("student")
def change_password():
    try:
        auth_service.change_password(g.user["id"], g.user["email"], request.form.get("current_password", ""),
                                     request.form.get("new_password", ""), request.form.get("confirm_password", ""),
                                     request.remote_addr)
    except auth_service.PasswordChangeError as exc:
        return render_template("student/profile.html", profile=profile_service.get_profile_details(g.user["id"]),
                               phone_error=None, pw_errors=exc.errors), 400
    uid = g.user["id"]
    session.clear()                                       # fresh session after a credential change
    session["user_id"] = uid
    session.permanent = True
    flash("Password changed.", "success")
    return redirect(url_for("student.profile"))
