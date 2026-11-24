"""Friendly error pages. Internal details are logged, never shown."""
from flask import current_app, render_template
from flask_wtf.csrf import CSRFError
from mysql.connector import errors as mysql_errors
from werkzeug.exceptions import HTTPException

from app.api import api_error
from app.security import wants_json
from app.services.errors import ServiceError

MESSAGES = {
    400: ("Bad request", "The request could not be understood."),
    403: ("Access denied", "You do not have permission to view this page."),
    404: ("Page not found", "The page you are looking for does not exist."),
    405: ("Method not allowed", "That action is not allowed here."),
    413: ("File too large", "The uploaded file is larger than the allowed size."),
    429: ("Too many requests", "Please wait a moment and try again."),
    500: ("Something went wrong", "An unexpected error occurred. It has been logged."),
    503: ("Service unavailable", "The database is not reachable right now. Please try again shortly."),
}


def _respond(code, message=None):
    title, default_message = MESSAGES.get(code, ("Error", "Something went wrong."))
    message = message or default_message
    if wants_json():
        return api_error(title.lower().replace(" ", "_"), message, code)
    return render_template("errors/error.html", code=code, title=title, message=message), code


def init_app(app):
    @app.errorhandler(ServiceError)
    def handle_service(exc):
        if wants_json():
            return api_error(exc.code, exc.message, exc.status, retry_after=exc.retry_after, fields=exc.fields)
        return _respond(exc.status, exc.message)

    @app.errorhandler(CSRFError)
    def handle_csrf(exc):
        return _respond(400, "Your session expired or the form was invalid. "
                             "Please reload the page and try again.")

    @app.errorhandler(HTTPException)
    def handle_http(exc):
        return _respond(exc.code or 500)

    @app.errorhandler(mysql_errors.Error)
    def handle_db(exc):
        current_app.logger.exception("Database error")
        return _respond(503)

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        current_app.logger.exception("Unhandled exception")
        return _respond(500)
