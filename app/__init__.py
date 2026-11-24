"""NEXORA application factory."""
from flask import Flask

from app import db, errors, navigation, security, template_filters
from app.config import Config, validate_config
from app.extensions import csrf


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(config_object or Config)
    validate_config(app.config)
    hops = app.config.get("TRUSTED_PROXY_HOPS", 0)
    if hops:                         # client IPs (rate limiting, audit log) must come from the proxy, not the proxy itself
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    csrf.init_app(app)
    db.init_app(app)
    security.init_app(app)
    navigation.init_app(app)
    errors.init_app(app)
    template_filters.init_app(app)

    from app.routes import (academics, admin, attendance, auth, fees, library, main, notices, student_api,
                            students, syllabus, teachers)
    for module in (main, auth, students, teachers, admin, attendance, academics, library, syllabus,
                   notices, fees):
        app.register_blueprint(module.bp)
    app.register_blueprint(attendance.api_bp)
    app.register_blueprint(student_api.api_bp)

    return app
