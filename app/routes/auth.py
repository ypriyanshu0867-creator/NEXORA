from flask import (Blueprint, flash, g, make_response, redirect, render_template, request,
                   session, url_for)

from app.db import query_all, transaction
from app.security import dashboard_url_for, login_required, safe_next_url
from app.services import audit_service, auth_service, device_service

bp = Blueprint("auth", __name__)

_FAIL_STATUS = {"rate_limited": 429, "invalid": 401, "device_cooldown": 429, "device_required": 400}


def _login_page(email, error, next_url, status=200, retry_after=None):
    response = make_response(render_template("auth/login.html", email=email, error=error,
                                             next_url=next_url), status)
    device_service.ensure_cookie(response)         # every login page carries a device cookie
    if retry_after:
        response.headers["Retry-After"] = str(int(retry_after))
    return response


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(dashboard_url_for(g.user["role"]))
    next_url = request.values.get("next", "")

    if request.method == "POST":
        email = request.form.get("email", "")
        result = auth_service.authenticate(email, request.form.get("password", ""),
                                           request.remote_addr, device_service.cookie_hash())
        if result.ok:
            session.clear()                      # prevents session fixation
            session["user_id"] = result.user["id"]
            session.permanent = True
            flash(f"Welcome back, {result.user['full_name'].split()[0]}.", "success")
            return redirect(safe_next_url(next_url) or dashboard_url_for(result.user["role"]))
        return _login_page(email, result.message, next_url,
                           _FAIL_STATUS.get(result.code, 403), result.retry_after)

    return _login_page("", None, next_url)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(dashboard_url_for(g.user["role"]))
    departments = query_all("SELECT id, code, name FROM departments ORDER BY name")
    form, errors = {"role": "student"}, {}

    if request.method == "POST":
        cleaned, errors = auth_service.validate_registration(
            request.form, {d["id"] for d in departments})
        if not errors:
            try:
                auth_service.register_user(cleaned, request.remote_addr)
            except auth_service.RegistrationError as exc:
                errors = exc.errors
            else:
                flash("Registration received. An administrator must approve your "
                      "account before you can sign in.", "success")
                return redirect(url_for("auth.login"))
        form = {k: request.form.get(k, "") for k in
                ("full_name", "email", "role", "department_id", "identifier")}
        return render_template("auth/register.html", departments=departments,
                               form=form, errors=errors), 400

    return render_template("auth/register.html", departments=departments,
                           form=form, errors=errors)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    with transaction():
        audit_service.log("logout", user_id=g.user["id"], ip=request.remote_addr)
        if g.user["role"] == "student":           # starts the device cooldown
            device_service.on_logout(device_service.cookie_hash(), g.user["id"], request.remote_addr)
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
