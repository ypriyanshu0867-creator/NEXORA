"""Shared extension instances (created here, attached in create_app)."""
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()
