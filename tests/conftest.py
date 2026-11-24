"""Shared pytest setup.

Safety: tests always use TestConfig, whose database is TEST_DB_NAME. The
config validator refuses to start if that equals the real DB_NAME.
"""
import os

# Forced (not setdefault) so a real .env can never leak a production secret into tests.
os.environ["SECRET_KEY"] = "test-only-secret-key-" + "0123456789abcdef" * 2

import pytest  # noqa: E402

from app import create_app  # noqa: E402
from app.config import TestConfig  # noqa: E402
from app.db import execute, get_db  # noqa: E402


class UnitTestConfig(TestConfig):
    """CSRF on, never touches MySQL. DB_USER only has to be non-empty."""
    DB_USER = TestConfig.DB_USER or "unit_test_user"


class IntegrationConfig(TestConfig):
    """Real MySQL test database; CSRF off so tests can post forms directly."""
    WTF_CSRF_ENABLED = False


@pytest.fixture
def unit_app():
    return create_app(UnitTestConfig)


@pytest.fixture(scope="session")
def integration_app():
    app = create_app(IntegrationConfig)
    try:
        with app.app_context():
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            cur.fetchall()
    except Exception as exc:                       # no MySQL, or tables missing
        pytest.skip(f"MySQL test database not available: {exc}")
    return app


def wipe_database(app):
    """Empty every table of the TEST database, then add one department."""
    with app.app_context():
        assert "test" in app.config["DB_NAME"].lower()  # last line of defence
        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'"
        )
        names = [
            r[0].decode() if isinstance(r[0], (bytes, bytearray)) else r[0]
            for r in cur.fetchall()
        ]

        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            for name in names:
                # Preserve migration history between integration tests.
                if name == "schema_migrations":
                    continue

                # Append-only tables are protected by triggers.
                verb = (
                    "TRUNCATE TABLE"
                    if name in ("audit_logs", "attendance_corrections")
                    else "DELETE FROM"
                )
                cur.execute(f"{verb} `{name}`")
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            cur.close()

        execute(
            "INSERT INTO departments (code, name) VALUES ('AIDS', 'AI and Data Science')"
        )
@pytest.fixture
def client(integration_app):
    wipe_database(integration_app)
    return integration_app.test_client()
