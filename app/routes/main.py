from flask import Blueprint, current_app, g, jsonify, redirect, url_for

from app.db import query_one
from app.security import dashboard_url_for

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    if g.user:
        return redirect(dashboard_url_for(g.user["role"]))
    return redirect(url_for("auth.login"))


@bp.route("/health")
def health():
    """Reports whether the app can really reach MySQL (SELECT 1)."""
    try:
        query_one("SELECT 1 AS ok")
    except Exception:
        current_app.logger.exception("Health check failed")
        return jsonify(status="error", database="unavailable"), 503
    return jsonify(status="ok", database="connected")
