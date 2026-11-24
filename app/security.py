"""Current-user loading, role-based access control and security headers."""
from functools import wraps
from urllib.parse import urlparse

from flask import abort, current_app, flash, g, redirect, request, session, url_for

from app.api import api_error
from app.db import query_one

DASHBOARD_ENDPOINTS = {
    "student": "student.dashboard",
    "teacher": "teacher.dashboard",
    "admin": "admin.dashboard",
}

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


def dashboard_url_for(role):
    return url_for(DASHBOARD_ENDPOINTS[role])


def safe_next_url(target):
    """Allow only same-site relative paths (prevents open redirects)."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    return target


def wants_json():
    return (request.is_json
            or request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json")


def load_current_user():
    """Load the signed-in user from the database on every request.

    The role is always read from the database, never from the session
    cookie or from anything the browser sends, so a role change or a
    suspension takes effect immediately.
    """
    g.user = None
    user_id = session.get("user_id")
    if user_id is None or request.endpoint == "static":
        return
    user = query_one(
        "SELECT id, email, full_name, role, status FROM users WHERE id = %s",
        (user_id,))
    if user is None or user["status"] != "active":
        session.clear()
        return
    g.user = user
    if user["role"] == "student":
        from app.services import device_service
        device_service.touch_if_due(user["id"])


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            if wants_json():
                return api_error("authentication_required", "Please sign in to continue.", 401)
            flash("Please sign in to continue.", "warning")
            target = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("auth.login", next=target))
        return view(*args, **kwargs)
    wrapped.nx_access = ("login", ())            # read by tests/test_static_checks.py (route-guard audit)
    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                if wants_json():
                    return api_error("forbidden", "You do not have permission to do that.", 403)
                abort(403)
            return view(*args, **kwargs)
        wrapped.nx_access = ("role", tuple(roles))
        return wrapped
    return decorator


def set_security_headers(response):
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(self), geolocation=(self), microphone=()"
    if current_app.config.get("USE_HTTPS") or current_app.config["APP_ENV"] == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


def init_app(app):
    app.before_request(load_current_user)
    app.after_request(set_security_headers)
