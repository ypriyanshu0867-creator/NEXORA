"""PHASE 2 UNIT TESTS - no MySQL needed.

These check validation rules, hashing, redirects, templates, security
headers and access guards. They do NOT prove anything about MySQL.
"""
from werkzeug.security import check_password_hash

from app.navigation import build_nav
from app.security import safe_next_url
from app.services import auth_service as auth

GOOD = {"full_name": "Test User", "email": "Test@Example.com", "password": "Passw0rd!",
        "confirm_password": "Passw0rd!", "role": "student", "department_id": "1",
        "identifier": "AIDS2026-009"}


def test_registration_accepts_valid_input_and_normalises_email():
    cleaned, errors = auth.validate_registration(GOOD, {1})
    assert errors == {}
    assert cleaned["email"] == "test@example.com"


def test_registration_rejects_admin_role_and_any_unknown_role():
    for role in ("admin", "ADMIN", "superuser", ""):
        _, errors = auth.validate_registration(dict(GOOD, role=role), {1})
        assert "role" in errors, role


def test_registration_rejects_unknown_department_bad_email_and_mismatch():
    assert "department_id" in auth.validate_registration(dict(GOOD, department_id="99"), {1})[1]
    assert "email" in auth.validate_registration(dict(GOOD, email="not-an-email"), {1})[1]
    assert "confirm_password" in auth.validate_registration(dict(GOOD, confirm_password="Other123!"), {1})[1]


def test_password_policy():
    assert auth.validate_password("Short1")                       # too short
    assert auth.validate_password("onlyletters")                  # no digit
    assert auth.validate_password("12345678")                     # no letter
    assert auth.validate_password("a" * 129 + "1")                # too long
    assert auth.validate_password("Same1234", email="same1234")   # equals email
    assert auth.validate_password("GoodPass1") is None


def test_password_hash_is_not_plaintext_and_verifies():
    hashed = auth.hash_password("Secret123")
    assert "Secret123" not in hashed
    assert hashed.startswith("pbkdf2:sha256:600000")
    assert check_password_hash(hashed, "Secret123")
    assert not check_password_hash(hashed, "secret123")


def test_safe_next_url_blocks_open_redirects():
    assert safe_next_url("/student/") == "/student/"
    for bad in ("", None, "//evil.com", "https://evil.com", "/\\evil.com", "evil.com", "javascript:alert(1)"):
        assert safe_next_url(bad) is None, bad


def test_all_templates_compile(unit_app):
    for name in unit_app.jinja_env.list_templates():
        unit_app.jinja_env.get_template(name)


def test_login_page_renders_with_security_headers(unit_app):
    response = unit_app.test_client().get("/login")
    assert response.status_code == 200
    assert b"Sign in" in response.data
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


def test_anonymous_users_are_sent_to_login(unit_app):
    client = unit_app.test_client()
    for path in ("/student/", "/teacher/", "/admin/", "/admin/approvals"):
        response = client.get(path)
        assert response.status_code == 302 and "/login" in response.headers["Location"], path


def test_logout_requires_post(unit_app):
    assert unit_app.test_client().get("/logout").status_code == 405


def test_navigation_greys_out_routes_that_do_not_exist_yet(unit_app):
    with unit_app.test_request_context("/student/"):
        items = {i["label"]: i for i in build_nav("student")}
        assert items["Dashboard"]["available"] and items["Dashboard"]["url"] == "/student/"
        admin = {i["label"]: i for i in build_nav("admin")}
        assert admin["Users"]["available"] and admin["Users"]["url"] == "/admin/users"
        assert {i["label"]: i for i in build_nav("admin")}["Pending Approvals"]["available"]


def test_csrf_blocks_post_without_token(unit_app):
    """Needs the real Flask-WTF package (CSRF is enabled in UnitTestConfig)."""
    response = unit_app.test_client().post("/login", data={"email": "a@b.co", "password": "x"})
    assert response.status_code == 400
