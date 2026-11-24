"""STATIC AND STRUCTURAL CHECKS (Phase 6) - no MySQL needed.

* every SQL string in app/ and scripts/ is consistent with the schema (tables, columns, placeholder counts)
* the checkers themselves are proven to catch mistakes (mutation tests)
* every route is guarded (or deliberately public) and guards match the URL space
* links, assets, CSP rules, secrets hygiene, error handling and debug settings

These prove names and structure only. They do NOT prove a query returns the right rows; that needs the
MySQL integration tests (tests/test_phase*_integration.py).
"""
import ast
import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lint_sql  # noqa: E402

PUBLIC = {"static", "main.index", "main.health", "auth.login", "auth.register"}


# ------------------------------------------------------------------ SQL lint
def test_every_sql_statement_matches_the_schema():
    problems, checked, tables, fks = lint_sql.lint()
    assert checked > 150 and tables >= 32 and fks >= 50
    assert not problems, "\n".join(problems)


def _problems(sql, params_src=None):
    tables, _ = lint_sql.build_schema()
    params = ast.parse(params_src).body[0].value if params_src else None
    return lint_sql.check_statement("test", sql, params, tables)


def test_linter_catches_mistakes():
    assert _problems("SELECT u.id FROM users u JOIN student_profiles sp ON sp.user_id = u.id WHERE u.id = %s", "(1,)") == []
    assert any("u.nme" in p for p in _problems("SELECT u.nme FROM users u WHERE u.id = %s", "(1,)"))
    assert any("userz" in p for p in _problems("SELECT id FROM userz WHERE id = %s", "(1,)"))
    assert any("columns but" in p for p in _problems("INSERT INTO users (email, full_name) VALUES (%s, %s, %s)", "(1, 2, 3)"))
    assert any("rolee" in p for p in _problems("UPDATE users SET rolee = %s WHERE id = %s", "(1, 2)"))
    assert any("placeholders" in p for p in _problems("SELECT id FROM users WHERE id = %s AND email = %s", "(1,)"))
    assert any("INSERT column" in p for p in _problems("INSERT INTO users (email, nope) VALUES (%s, %s)", "(1, 2)"))
    assert any("no parameters" in p for p in _problems("SELECT id FROM users WHERE id = %s"))
    # columns added by migrations are known; the row-lock clause is not mistaken for a table
    assert _problems("SELECT s.id FROM attendance_sessions s WHERE s.active_assignment_id = %s FOR UPDATE OF s", "(1,)") == []
    assert _problems("UPDATE notices SET class_id = %s, is_pinned = %s WHERE id = %s", "(1, 0, 3)") == []


def test_schema_rules_catch_mysql_restrictions():
    lint_sql.check_conflicts.clear()
    tables, fks = {}, []
    lint_sql.parse_create_tables(
        "CREATE TABLE t (id INT, parent INT NULL, CONSTRAINT fk FOREIGN KEY (parent) REFERENCES t (id) ON DELETE SET NULL, "
        "CONSTRAINT ck CHECK (parent IS NULL OR parent > 0)) ENGINE=InnoDB", tables, fks)
    assert lint_sql.check_conflicts == [("t", "ck", "parent")]
    lint_sql.check_conflicts.clear()
    assert any("unknown table" in p for p in lint_sql.check_foreign_keys({"a": {"x"}}, [("a", "x", "missing", "id")]))
    assert any("unknown column" in p for p in lint_sql.check_foreign_keys({"a": {"x"}, "b": {"id"}}, [("a", "x", "b", "nope")]))


def test_real_schema_has_no_check_or_foreign_key_conflicts():
    lint_sql.check_conflicts.clear()
    lint_sql.build_schema()
    assert lint_sql.check_conflicts == []


def test_migrations_are_numbered_in_order_and_have_rollbacks():
    names = sorted(p.name for p in (ROOT / "database" / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
    assert names and [n[:3] for n in names] == [f"{i:03d}" for i in range(1, len(names) + 1)]
    for n in names:
        assert (ROOT / "database" / "migrations" / "rollback" / n.replace(".sql", ".rollback.sql")).exists(), n


# ------------------------------------------------------------- route guards
def test_every_endpoint_is_guarded_or_deliberately_public(unit_app):
    unguarded = [r.rule for r in unit_app.url_map.iter_rules()
                 if r.endpoint not in PUBLIC and not hasattr(unit_app.view_functions[r.endpoint], "nx_access")]
    assert not unguarded, unguarded


ROLE_BY_PREFIX = [("/teacher", ("teacher",)), ("/api/teacher", ("teacher",)), ("/admin", ("admin",)),
                  ("/student", ("student",)), ("/api/student", ("student",)), ("/academics", ("student",)),
                 ("/fees/manage", ("admin",)), ("/fees", ("student",)), ("/attendance", ("student",)), ("/api/attendance", ("student",)),
                  ("/library/my-books", ("student",))]


def test_role_guards_match_the_url_space(unit_app):
    for rule in unit_app.url_map.iter_rules():
        matches = [(p, roles) for p, roles in ROLE_BY_PREFIX if rule.rule.startswith(p)]
        if not matches:
            continue
        prefix, roles = max(matches, key=lambda m: len(m[0]))
        access = unit_app.view_functions[rule.endpoint].nx_access
        assert access == ("role", roles), (rule.rule, access, roles)


def test_get_only_routes_never_call_write_services(unit_app):
    verbs = re.compile(r"\.(save_|set_|add_|update_|apply_|change_|review_|start_|close_|issue_|register_|mark_|correct)\w*\(")
    offenders = []
    for rule in unit_app.url_map.iter_rules():
        if "POST" in rule.methods or rule.endpoint == "static":
            continue
        source = inspect.getsource(inspect.unwrap(unit_app.view_functions[rule.endpoint]))
        if verbs.search(source):
            offenders.append(rule.rule)
    assert not offenders, offenders


def test_unauthenticated_requests_never_reach_a_view(unit_app):
    client = unit_app.test_client()
    for rule in unit_app.url_map.iter_rules():
        if rule.endpoint in PUBLIC or "GET" not in rule.methods:
            continue
        path = re.sub(r"<[^>]+>", "1", rule.rule)
        response = client.get(path)
        assert response.status_code in (302, 401), (rule.rule, response.status_code)


# -------------------------------------------------------- links and assets
def _sources():
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "app" / "templates").rglob("*.html")):
        yield path, path.read_text(encoding="utf8")


def test_all_url_for_targets_exist(unit_app):
    endpoints = set(unit_app.view_functions)
    missing = []
    for path, text in _sources():
        for name in re.findall(r"url_for\(\s*['\"]([\w.]+)['\"]", text):
            if name not in endpoints:
                missing.append(f"{path.relative_to(ROOT)}: {name}")
    assert not missing, missing


def test_static_files_referenced_by_templates_exist():
    missing = []
    for path, text in _sources():
        for name in re.findall(r"url_for\(\s*['\"]static['\"]\s*,\s*filename=['\"]([^'\"]+)['\"]", text):
            if not (ROOT / "app" / "static" / name).exists():
                missing.append(f"{path.relative_to(ROOT)}: {name}")
    assert not missing, missing


def test_templates_have_no_inline_scripts_or_styles():
    """The Content-Security-Policy blocks them, so they would silently break at run time."""
    bad = []
    for path in (ROOT / "app" / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf8")
        if re.search(r"\sstyle=|<script(?![^>]*\ssrc=)|\son(click|submit|change|load)=", text):
            bad.append(path.name)
    assert not bad, bad


def test_dashboards_and_menus_have_no_dead_links(unit_app):
    from app.navigation import NAV_ITEMS
    for role in ("student", "teacher"):
        missing = [label for label, endpoint, _ in NAV_ITEMS[role] if endpoint not in unit_app.view_functions]
        assert not missing, (role, missing)
    admin = {label: endpoint in unit_app.view_functions for label, endpoint, _ in NAV_ITEMS["admin"]}
    assert admin["Dashboard"] and admin["Pending Approvals"]


# ------------------------------------------------- secrets, errors, debug
def test_secrets_hygiene():
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf8").splitlines()
    example = (ROOT / ".env.example").read_text(encoding="utf8")
    assert "SECRET_KEY=change-me" in example and "DB_PASSWORD=change-me" in example
    for path, text in _sources():
        assert "Nexora@2026" not in text, f"demo password in application code: {path}"
        assert not re.search(r"(?i)(secret_key|db_password)\s*=\s*['\"][^'\"]{6,}['\"]", text), path


def test_password_hashes_never_reach_templates_or_routes():
    for path in list((ROOT / "app" / "routes").glob("*.py")) + list((ROOT / "app" / "templates").rglob("*.html")):
        assert "password_hash" not in path.read_text(encoding="utf8"), path


def test_debug_is_never_enabled_on_a_network_interface(unit_app):
    run_py = (ROOT / "run.py").read_text(encoding="utf8")
    assert 'host in ("127.0.0.1", "localhost")' in run_py and "debug=debug" in run_py
    assert unit_app.debug is False


def test_unexpected_errors_do_not_leak_internals(unit_app):
    from mysql.connector import errors as mysql_errors
    unit_app.add_url_rule("/_boom", "boom", lambda: 1 / 0)
    unit_app.add_url_rule("/_dbboom", "dbboom", lambda: (_ for _ in ()).throw(mysql_errors.Error("Access denied for user 'nexora_app'@'10.0.0.5'")))
    client = unit_app.test_client()
    for path, status in (("/_boom", 500), ("/_dbboom", 503)):
        for headers in ({}, {"Accept": "application/json"}):
            response = client.get(path, headers=headers)
            body = response.get_data(as_text=True)
            assert response.status_code == status, (path, response.status_code)
            for secret in ("Traceback", "ZeroDivisionError", "Access denied", "nexora_app", "10.0.0.5", str(ROOT)):
                assert secret not in body, (path, secret)


def test_security_headers_and_production_hsts(unit_app):
    client = unit_app.test_client()
    headers = client.get("/login").headers
    assert "default-src 'self'" in headers["Content-Security-Policy"] and headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" not in headers
    unit_app.config["APP_ENV"] = "production"
    assert "Strict-Transport-Security" in client.get("/login").headers


def test_session_and_device_cookie_settings(unit_app):
    cfg = unit_app.config
    assert cfg["SESSION_COOKIE_HTTPONLY"] is True and cfg["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert cfg["PERMANENT_SESSION_LIFETIME"].total_seconds() <= 24 * 3600
    assert cfg["MAX_CONTENT_LENGTH"] <= 20 * 1024 * 1024


# ----------------------------------------------- CSRF, XSS and SQL-injection guards
def test_every_post_form_carries_a_csrf_token():
    missing = []
    for path in (ROOT / "app" / "templates").rglob("*.html"):
        for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", path.read_text(encoding="utf8"), re.S | re.I):
            if re.search(r'method\s*=\s*"post"', m.group(1), re.I) and "csrf_token" not in m.group(2):
                missing.append(f"{path.name}: {m.group(1)[:60]}")
    assert not missing, missing


def test_ajax_code_sends_the_csrf_header():
    for name in ("teacher-qr.js", "scanner.js", "calculator.js"):
        text = (ROOT / "app" / "static" / "js" / name).read_text(encoding="utf8")
        assert "X-CSRFToken" in text, name


def test_no_xss_prone_constructs():
    patterns = [r"\|\s*safe", r"autoescape\s+false", r"Markup\(", r"render_template_string", r"innerHTML", r"outerHTML",
                r"insertAdjacentHTML", r"document\.write", r"\beval\("]
    hits = []
    files = list((ROOT / "app" / "templates").rglob("*.html")) + list((ROOT / "app" / "static" / "js").glob("*.js")) \
        + list((ROOT / "app").rglob("*.py"))
    for path in files:
        text = path.read_text(encoding="utf8")
        hits += [f"{path.name}: {p}" for p in patterns if re.search(p, text)]
    assert not hits, hits


# Every statement that is assembled at run time was reviewed by hand: the variable parts are fixed SQL fragments,
# column names from a fixed tuple, or runs of %s placeholders. User input only ever travels as parameters.
# If you add dynamic SQL, review it the same way and raise the number here.
REVIEWED_DYNAMIC_SQL = {
    "app/services/admin_audit_service.py": 2,
    "app/services/admin_fee_service.py": 2,
    "app/services/admin_service.py": 1,
    "app/services/auth_service.py": 1,
    "app/services/library_service.py": 2,
    "app/services/notice_service.py": 3,
    "app/services/result_service.py": 1,
    "app/services/teacher_notice_service.py": 1,
    "app/services/teacher_service.py": 5,
}


def test_dynamic_sql_is_limited_to_reviewed_statements():
    found = {}
    for folder in ("app", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            n = sum(1 for _, sql, _ in lint_sql.extract(path) if "{X}" in sql)
            if n:
                found[str(path.relative_to(ROOT)).replace("\\", "/")] = n
    assert found == REVIEWED_DYNAMIC_SQL, found


def test_reverse_proxy_headers_are_trusted_only_when_configured():
    from app import create_app
    from app.config import TestConfig
    from flask import request

    class Behind(TestConfig):
        DB_USER = TestConfig.DB_USER or "unit_test_user"
        TRUSTED_PROXY_HOPS = 1

    class Direct(Behind):
        TRUSTED_PROXY_HOPS = 0

    answers = {}
    for name, config in (("behind", Behind), ("direct", Direct)):
        app = create_app(config)
        app.add_url_rule("/_ip", "ip", lambda: request.remote_addr)
        answers[name] = app.test_client().get("/_ip", headers={"X-Forwarded-For": "203.0.113.7"}).get_data(as_text=True)
    assert answers["behind"] == "203.0.113.7" and answers["direct"] != "203.0.113.7"        # a spoofed header is ignored without a proxy
