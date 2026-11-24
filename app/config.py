"""Application configuration.

Every secret and environment-specific value comes from environment
variables (loaded from the .env file in the project root).
"""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unsafe."""


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}.") from None


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


_APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
_USE_HTTPS = _env_bool("FLASK_HTTPS")


class Config:
    APP_ENV = _APP_ENV
    SECRET_KEY = os.getenv("SECRET_KEY", "")

    # --- Database -------------------------------------------------------
    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = _env_int("DB_PORT", 3306)
    DB_USER = os.getenv("DB_USER", "")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "nexora")
    MAIN_DB_NAME = os.getenv("DB_NAME", "nexora")      # never changed by TestConfig
    TEST_DB_NAME = os.getenv("TEST_DB_NAME", "nexora_test")
    DB_POOL_SIZE = _env_int("DB_POOL_SIZE", 10)

    # Number of reverse proxies in front of the app (0 = none). Only then are X-Forwarded-* headers trusted.
    TRUSTED_PROXY_HOPS = _env_int("TRUSTED_PROXY_HOPS", 0)

    # --- Sessions and cookies --------------------------------------------
    SESSION_COOKIE_NAME = "nexora_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _USE_HTTPS or _APP_ENV == "production"
    SESSION_REFRESH_EACH_REQUEST = True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_env_int("SESSION_HOURS", 2))
    USE_HTTPS = _USE_HTTPS

    # --- CSRF (Flask-WTF) -------------------------------------------------
    WTF_CSRF_TIME_LIMIT = None          # token lives as long as the session

    # --- Uploads ----------------------------------------------------------
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    UPLOAD_FOLDER = str(BASE_DIR / "instance" / "uploads")

    # --- Login rate limiting ---------------------------------------------
    LOGIN_MAX_FAILURES_PER_EMAIL = _env_int("LOGIN_MAX_FAILURES", 5)
    LOGIN_MAX_FAILURES_PER_IP = _env_int("LOGIN_MAX_FAILURES_PER_IP", 20)
    LOGIN_WINDOW_MINUTES = _env_int("LOGIN_WINDOW_MINUTES", 15)
    REGISTER_MAX_PER_IP_PER_HOUR = _env_int("REGISTER_MAX_PER_IP_PER_HOUR", 10)

    # --- Attendance defaults (used from Phase 3) ---------------------------
    QR_VALIDITY_SECONDS = _env_int("QR_VALIDITY_SECONDS", 120)
    DEVICE_COOLDOWN_SECONDS = _env_int("DEVICE_COOLDOWN_SECONDS", 180)
    SESSION_DEFAULT_MINUTES = _env_int("SESSION_DEFAULT_MINUTES", 10)
    ATTENDANCE_MAX_FAILURES_PER_MINUTE = _env_int("ATTENDANCE_MAX_FAILURES_PER_MINUTE", 10)
    # --- Student portal (Phase 4) ---------------------------------------------
    ATTENDANCE_THRESHOLD_PERCENT = _env_int("ATTENDANCE_THRESHOLD_PERCENT", 75)
    GPA_DECIMALS = _env_int("GPA_DECIMALS", 2)
    CGPA_REPEAT_POLICY = os.getenv("CGPA_REPEAT_POLICY", "latest").strip().lower()   # latest | best
    LIBRARY_FINE_PER_DAY = os.getenv("LIBRARY_FINE_PER_DAY", "0").strip()            # 0 = fines not configured
    PAGE_SIZE = _env_int("PAGE_SIZE", 12)
    # --- Teacher tools (Phase 5) ------------------------------------------------
    TEACHER_CAN_PUBLISH_RESULTS = _env_bool("TEACHER_CAN_PUBLISH_RESULTS", True)
    MATERIAL_MAX_BYTES = _env_int("MATERIAL_MAX_MB", 10) * 1024 * 1024
    DEVICE_COOKIE_NAME = "nexora_device"
    DEVICE_COOKIE_MAX_AGE_DAYS = _env_int("DEVICE_COOKIE_MAX_AGE_DAYS", 365)


class TestConfig(Config):
    """Used by pytest. Always points at the dedicated test database."""
    TESTING = True
    DB_NAME = Config.TEST_DB_NAME
    SESSION_COOKIE_SECURE = False


_PLACEHOLDER_KEYS = {"", "change-me", "change-me-to-a-long-random-string"}


def validate_config(cfg):
    """Fail early, with a clear message, on missing or unsafe settings."""
    key = cfg.get("SECRET_KEY", "")
    if key in _PLACEHOLDER_KEYS or len(key) < 32:
        raise ConfigError(
            "SECRET_KEY is missing, still the placeholder, or shorter than 32 "
            "characters. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_hex(32))"\n'
            "and put it in your .env file as SECRET_KEY=...")
    if not cfg.get("DB_USER"):
        raise ConfigError("DB_USER is not set. Copy .env.example to .env and fill it in.")
    if not cfg.get("DB_NAME"):
        raise ConfigError("DB_NAME is not set.")
    if cfg.get("CGPA_REPEAT_POLICY") not in ("latest", "best"):
        raise ConfigError("CGPA_REPEAT_POLICY must be 'latest' or 'best'.")
    from decimal import Decimal, InvalidOperation
    try:
        if Decimal(cfg.get("LIBRARY_FINE_PER_DAY", "0")) < 0:
            raise InvalidOperation
    except InvalidOperation:
        raise ConfigError("LIBRARY_FINE_PER_DAY must be a number of zero or more.") from None
    if cfg.get("TESTING"):
        if cfg["DB_NAME"] == cfg["MAIN_DB_NAME"]:
            raise ConfigError(
                "TEST_DB_NAME must be different from DB_NAME. Tests must never "
                "run against your real database.")
        if "test" not in cfg["DB_NAME"].lower():
            raise ConfigError("The test database name must contain the word 'test'.")
