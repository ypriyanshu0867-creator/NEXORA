r"""PHASE 2 INTEGRATION TESTS - require a real MySQL test database.

Prepare once:   python scripts\init_db.py --bootstrap
                python scripts\init_db.py --test
Run:            python -m pytest tests -v
They are skipped automatically when MySQL is not reachable. The test
database is emptied before every test; the real database is never used.
"""
import pytest

from app.db import execute, query_one, transaction
from app.services import auth_service

pytestmark = pytest.mark.integration

PASSWORD = "Passw0rd1"


def create_user(app, email, role, status="active"):
    with app.app_context():
        uid = execute(
            "INSERT INTO users (email, password_hash, full_name, role, status) VALUES (%s, %s, %s, %s, %s)",
            (email, auth_service.hash_password(PASSWORD), f"Test {role.title()}", role, status)).lastrowid
        dept = query_one("SELECT id FROM departments LIMIT 1")["id"]
        if role == "student":
            execute("INSERT INTO student_profiles (user_id, roll_number, department_id) VALUES (%s, %s, %s)",
                    (uid, f"ROLL-{uid:04d}", dept))
        elif role == "teacher":
            execute("INSERT INTO teacher_profiles (user_id, employee_code, department_id) VALUES (%s, %s, %s)",
                    (uid, f"EMP-{uid:04d}", dept))
        return uid


def register(client, app, **overrides):
    with app.app_context():
        dept = query_one("SELECT id FROM departments LIMIT 1")["id"]
    data = {"full_name": "New Student", "email": "new@college.test", "password": PASSWORD,
            "confirm_password": PASSWORD, "role": "student", "department_id": str(dept),
            "identifier": "AIDS2026-100"}
    data.update(overrides)
    return client.post("/register", data=data)


def login(client, email, password=PASSWORD):
    client.get("/login")          # a browser always loads the page first; it receives the device cookie
    return client.post("/login", data={"email": email, "password": password})


def user_row(app, email):
    with app.app_context():
        return query_one("SELECT * FROM users WHERE email = %s", (email,))


def test_health_reports_database_connected(client):
    response = client.get("/health")
    assert response.status_code == 200 and response.get_json()["database"] == "connected"


def test_registration_creates_pending_account_and_blocks_login(client, integration_app):
    assert register(client, integration_app).status_code == 302
    row = user_row(integration_app, "new@college.test")
    assert row["status"] == "pending" and row["role"] == "student"
    assert row["password_hash"].startswith("pbkdf2:") and PASSWORD not in row["password_hash"]
    response = login(client, "new@college.test")
    assert response.status_code == 403 and b"waiting for administrator approval" in response.data


def test_cannot_register_as_admin(client, integration_app):
    assert register(client, integration_app, role="admin").status_code == 400
    assert user_row(integration_app, "new@college.test") is None


def test_extra_form_fields_cannot_escalate_privileges(client, integration_app):
    register(client, integration_app, status="active", is_admin="1", approved_by="1")
    row = user_row(integration_app, "new@college.test")
    assert row["role"] == "student" and row["status"] == "pending" and row["approved_by"] is None


def test_duplicate_email_is_rejected_case_insensitively(client, integration_app):
    register(client, integration_app, email="dup@college.test")
    response = register(client, integration_app, email="DUP@college.test", identifier="AIDS2026-101")
    assert response.status_code == 400 and b"already registered" in response.data
    with integration_app.app_context():
        assert query_one("SELECT COUNT(*) AS c FROM users")["c"] == 1


def test_admin_approval_enables_login_and_is_audited(client, integration_app):
    create_user(integration_app, "boss@college.test", "admin")
    register(client, integration_app)
    pending_id = user_row(integration_app, "new@college.test")["id"]

    assert login(client, "boss@college.test").status_code == 302
    assert client.post(f"/admin/approvals/{pending_id}/approve").status_code == 302
    client.post("/logout")

    assert user_row(integration_app, "new@college.test")["status"] == "active"
    response = login(client, "new@college.test")
    assert response.status_code == 302 and response.headers["Location"].endswith("/student/")
    with integration_app.app_context():
        assert query_one("SELECT COUNT(*) AS c FROM audit_logs WHERE action = 'user.approve'")["c"] == 1


def test_student_cannot_open_admin_or_teacher_pages(client, integration_app):
    create_user(integration_app, "s@college.test", "student")
    login(client, "s@college.test")
    assert client.get("/student/").status_code == 200
    assert client.get("/admin/").status_code == 403
    assert client.get("/teacher/").status_code == 403
    assert client.post("/admin/approvals/1/approve").status_code == 403


def test_role_is_never_taken_from_the_request(client, integration_app):
    create_user(integration_app, "s@college.test", "student")
    client.get("/login")
    response = client.post("/login", data={"email": "s@college.test", "password": PASSWORD, "role": "admin"})
    assert response.headers["Location"].endswith("/student/")
    assert client.get("/admin/").status_code == 403


def test_login_rate_limit_locks_after_repeated_failures(client, integration_app):
    create_user(integration_app, "s@college.test", "student")
    for _ in range(5):
        assert login(client, "s@college.test", "wrong-password1").status_code == 401
    assert login(client, "s@college.test").status_code == 429          # even the right password


def test_logout_ends_the_session(client, integration_app):
    create_user(integration_app, "s@college.test", "student")
    login(client, "s@college.test")
    assert client.post("/logout").status_code == 302
    assert client.get("/student/").status_code == 302


def test_suspending_a_user_invalidates_their_session_immediately(client, integration_app):
    uid = create_user(integration_app, "s@college.test", "student")
    login(client, "s@college.test")
    with integration_app.app_context():
        execute("UPDATE users SET status = 'suspended' WHERE id = %s", (uid,))
    assert client.get("/student/").status_code == 302


def test_transaction_rolls_back_on_error(integration_app, client):
    with integration_app.app_context():
        before = query_one("SELECT COUNT(*) AS c FROM departments")["c"]
        with pytest.raises(RuntimeError):
            with transaction():
                execute("INSERT INTO departments (code, name) VALUES ('TMP', 'Temporary')")
                raise RuntimeError("boom")
        assert query_one("SELECT COUNT(*) AS c FROM departments")["c"] == before
