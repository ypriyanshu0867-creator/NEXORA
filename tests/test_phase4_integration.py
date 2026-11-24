r"""PHASE 4 INTEGRATION TESTS - require a real MySQL test database.

Prepare once:   python scripts\init_db.py --test     (applies migrations 001 and 002)
Run:            python -m pytest tests -v
Skipped automatically when MySQL is unreachable. The test database is emptied
before every test; the real database is never used.
"""
import datetime
import io
from decimal import Decimal as D

import pytest

from app.db import execute, query_all, query_one
from app.services import auth_service

pytestmark = pytest.mark.integration
PASSWORD = "Passw0rd1"
NOW = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)
TODAY = NOW.date()


def days(n):
    return NOW + datetime.timedelta(days=n)


def pdf(folder, name, content=b"%PDF-1.4\nok\n%%EOF"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(content)


@pytest.fixture
def w(client, integration_app, tmp_path, monkeypatch):
    """Two AI&DS students (s1, s2), one CSE student (s3), a teacher, subjects and a default grading scheme."""
    monkeypatch.setitem(integration_app.config, "UPLOAD_FOLDER", str(tmp_path))
    with integration_app.app_context():
        aids = query_one("SELECT id FROM departments LIMIT 1")["id"]
        cse = execute("INSERT INTO departments (code, name) VALUES ('CSE', 'Computer Science')").lastrowid
        ay_old = execute("INSERT INTO academic_years (label, start_date, end_date, is_current) VALUES ('2025-26', '2025-06-01', '2026-05-31', 0)").lastrowid
        ay = execute("INSERT INTO academic_years (label, start_date, end_date, is_current) VALUES ('2026-27', '2026-06-01', '2027-05-31', 1)").lastrowid
        sem1 = execute("INSERT INTO semesters (academic_year_id, semester_number, name, start_date, end_date, is_current) VALUES (%s, 1, 'Semester 1', '2025-06-01', '2025-11-30', 0)", (ay_old,)).lastrowid
        sem3 = execute("INSERT INTO semesters (academic_year_id, semester_number, name, start_date, end_date, is_current) VALUES (%s, 3, 'Semester 3', '2026-06-01', '2026-12-31', 1)", (ay,)).lastrowid
        cls = execute("INSERT INTO classes (department_id, academic_year_id, name, section) VALUES (%s, %s, 'SE', 'A')", (aids, ay)).lastrowid
        subj = {}
        for code, dept, number, credits in (("S1", aids, 1, 4), ("S2", aids, 1, 3), ("S3", aids, 3, 4), ("S5", aids, 5, 3), ("C1", cse, 1, 3)):
            subj[code] = execute("INSERT INTO subjects (code, name, department_id, semester_number, credits) VALUES (%s, %s, %s, %s, %s)",
                                 (code, f"Subject {code}", dept, number, credits)).lastrowid
        scheme = execute("INSERT INTO grading_schemes (name, is_default) VALUES ('Test scheme', 1)").lastrowid
        for label, lo, hi, gp, ok in (("O", 90, 100, 10, 1), ("A", 70, 89.99, 8, 1), ("B", 50, 69.99, 6, 1), ("F", 0, 49.99, 0, 0)):
            execute("INSERT INTO grading_scheme_rules (scheme_id, grade_label, min_percent, max_percent, grade_point, is_pass) VALUES (%s, %s, %s, %s, %s, %s)", (scheme, label, lo, hi, gp, ok))
        users = {}
        for key, email, role, dept in (("s1", "s1@x.test", "student", aids), ("s2", "s2@x.test", "student", aids), ("s3", "s3@x.test", "student", cse),
                                       ("t1", "t1@x.test", "teacher", aids), ("adm", "adm@x.test", "admin", None)):
            uid = execute("INSERT INTO users (email, password_hash, full_name, role, status) VALUES (%s, %s, %s, %s, 'active')",
                          (email, auth_service.hash_password(PASSWORD), f"User {key}", role)).lastrowid
            users[key] = uid
            if role == "student":
                execute("INSERT INTO student_profiles (user_id, roll_number, department_id, class_id, current_semester_id) VALUES (%s, %s, %s, %s, %s)",
                        (uid, f"R-{key}", dept, cls, sem3))
            elif role == "teacher":
                execute("INSERT INTO teacher_profiles (user_id, employee_code, department_id) VALUES (%s, %s, %s)", (uid, "E-1", dept))
        users.update(aids=aids, cse=cse, sem1=sem1, sem3=sem3, scheme=scheme, cls=cls, subj=subj, ay=ay, tmp=tmp_path)
    users["app"] = integration_app
    return users


def sign_in(w, email):
    c = w["app"].test_client()
    c.get("/login")
    assert c.post("/login", data={"email": email, "password": PASSWORD}).status_code == 302
    return c


def result(w, student, subject, semester, marks, grade, gp, published=1, when=None):
    with w["app"].app_context():
        execute("INSERT INTO academic_results (student_id, subject_id, semester_id, scheme_id, marks_obtained, max_marks, grade_label, "
                "grade_point, credits_snapshot, is_published, entered_at) VALUES (%s, %s, %s, %s, %s, 100, %s, %s, "
                "(SELECT credits FROM subjects WHERE id = %s), %s, %s)",
                (w[student], w["subj"][subject], w[semester], w["scheme"], marks, grade, gp, w["subj"][subject], published, when or NOW))


def run(w, sql, params=()):
    with w["app"].app_context():
        return execute(sql, params).lastrowid


# ================================ dashboard
def test_dashboard_requires_login_and_student_role(w):
    anon = w["app"].test_client()
    assert anon.get("/student/").status_code == 302 and anon.get("/api/student/dashboard").status_code == 401
    assert sign_in(w, "t1@x.test").get("/student/").status_code == 403
    assert sign_in(w, "adm@x.test").get("/student/").status_code == 403


def test_dashboard_with_no_records_shows_empty_states(w):
    page = sign_in(w, "s1@x.test").get("/student/")
    assert page.status_code == 200
    for text in (b"Not published", b"No fee records", b"No sessions yet", b"No notices", b"No activity yet"):
        assert text in page.data, text
    data = sign_in(w, "s1@x.test").get("/api/student/dashboard").get_json()["data"]
    assert data["cgpa"] is None and data["latest_sgpa"] is None and data["library"]["current"] == [] and data["fees"]["records"] == []


def test_dashboard_values_come_from_the_database(w):
    result(w, "s1", "S1", "sem1", 95, "O", 10)
    run(w, "INSERT INTO library_books (title, author, category, total_copies, available_copies) VALUES ('Book', 'Auth', 'Cat', 2, 1)")
    book = query_all_one(w, "SELECT id FROM library_books")["id"]
    run(w, "INSERT INTO library_issues (book_id, student_id, issued_on, due_on) VALUES (%s, %s, %s, %s)", (book, w["s1"], TODAY, TODAY + datetime.timedelta(days=14)))
    data = sign_in(w, "s1@x.test").get("/api/student/dashboard").get_json()["data"]
    assert data["cgpa"]["cgpa"] == "10.00" and len(data["library"]["current"]) == 1 and data["profile"]["roll_number"] == "R-s1"
    # another student's data never leaks in
    other = sign_in(w, "s2@x.test").get("/api/student/dashboard").get_json()["data"]
    assert other["cgpa"] is None and other["library"]["current"] == []


def query_all_one(w, sql, params=()):
    with w["app"].app_context():
        return query_one(sql, params)


# ================================ results and GPA
def test_student_sees_only_own_published_results(w):
    result(w, "s1", "S1", "sem1", 95, "O", 10)
    result(w, "s1", "S2", "sem1", 60, "B", 6)
    result(w, "s1", "S3", "sem3", 80, "A", 8, published=0)           # unpublished: must stay hidden
    result(w, "s2", "S1", "sem1", 40, "F", 0)
    s1 = sign_in(w, "s1@x.test")
    body = s1.get("/api/student/results").get_json()["data"]
    codes = [s["code"] for y in body["years"] for sem in y["semesters"] for s in sem["subjects"]]
    assert sorted(codes) == ["S1", "S2"] and body["not_published"] == ["Semester 3"]
    sem = body["years"][0]["semesters"][0]
    assert sem["gpa"] == "8.29" and D(sem["total_credits"]) == 7 and body["cgpa"]["cgpa"] == "8.29"
    page = s1.get("/academics/results")
    assert b"Semester 3</strong> have not been published" in page.data and b"80" not in page.data.split(b"Academic history")[0].replace(b"8.29", b"")
    other = sign_in(w, "s2@x.test").get("/api/student/results").get_json()["data"]
    assert [s["grade_label"] for y in other["years"] for sem in y["semesters"] for s in sem["subjects"]] == ["F"]
    assert other["years"][0]["semesters"][0]["subjects"][0]["status"] == "Fail"


def test_repeated_subject_follows_the_configured_policy(w):
    result(w, "s2", "S1", "sem1", 40, "F", 0, when=days(-300))
    result(w, "s2", "S1", "sem3", 75, "A", 8, when=days(-10))
    result(w, "s2", "S2", "sem1", 60, "B", 6)
    s2 = sign_in(w, "s2@x.test")
    latest = s2.get("/api/student/academic-history").get_json()["data"]
    assert latest["cgpa"] == "7.14" and latest["policy"] == "latest"          # (4*8 + 3*6) / 7
    w["app"].config["CGPA_REPEAT_POLICY"] = "best"
    try:
        assert s2.get("/api/student/academic-history").get_json()["data"]["cgpa"] == "7.14"
    finally:
        w["app"].config["CGPA_REPEAT_POLICY"] = "latest"


def test_sgpa_calculator_uses_configured_rules_and_labels_estimates(w):
    result(w, "s1", "S1", "sem1", 95, "O", 10)
    result(w, "s1", "S2", "sem1", 60, "B", 6)
    s1 = sign_in(w, "s1@x.test")
    sid = w["subj"]
    same = s1.post("/api/student/sgpa/calculate", json={"semester_number": 1, "grades": {str(sid["S1"]): "O", str(sid["S2"]): "B"}}).get_json()["data"]
    assert same["gpa"] == "8.29" and same["is_estimate"] is False and D(same["total_credits"]) == 7 and D(same["total_weighted"]) == 58
    what_if = s1.post("/api/student/sgpa/calculate", json={"semester_number": 1, "grades": {str(sid["S1"]): "O", str(sid["S2"]): "A"}}).get_json()["data"]
    assert what_if["gpa"] == "9.14" and what_if["is_estimate"] is True
    partial = s1.post("/api/student/sgpa/calculate", json={"semester_number": 1, "grades": {str(sid["S1"]): "O"}}).get_json()["data"]
    assert partial["not_included"][0]["code"] == "S2"
    manual = s1.post("/api/student/sgpa/calculate", json={"items": [{"credits": "4", "grade_point": "10"}, {"credits": "3", "grade_point": "6"}]}).get_json()["data"]
    assert manual["gpa"] == "8.29" and manual["is_estimate"] is True


def test_calculator_validation_and_isolation_from_other_departments(w):
    s1 = sign_in(w, "s1@x.test")
    sid = w["subj"]
    post = lambda body, path="sgpa": s1.post(f"/api/student/{path}/calculate", json=body)
    assert post({"items": [{"credits": "-3", "grade_point": "6"}]}).status_code == 422
    assert post({"items": [{"credits": "3", "grade_point": "7"}]}).status_code == 422          # 7 is not a point in the scheme
    assert post({"semester_number": 1, "grades": {str(sid["S1"]): "Z"}}).status_code == 422
    assert post({"semester_number": 1, "grades": {str(sid["C1"]): "O"}}).status_code == 422    # other department's subject
    assert post({"semester_number": 9, "grades": {}}).status_code == 422
    assert post({}, "cgpa").status_code == 404                                                   # no results, nothing entered


def test_cgpa_calculator_adds_a_hypothetical_semester(w):
    result(w, "s1", "S1", "sem1", 95, "O", 10)
    r = sign_in(w, "s1@x.test").post("/api/student/cgpa/calculate", json={"hypothetical": {"semester_number": 3, "grades": {str(w["subj"]["S3"]): "B"}}})
    data = r.get_json()["data"]
    assert data["gpa"] == "8.00" and data["is_estimate"] is True                                  # (4*10 + 4*6) / 8


def test_missing_grading_scheme_is_reported(w):
    run(w, "DELETE FROM grading_schemes")
    r = sign_in(w, "s1@x.test").post("/api/student/sgpa/calculate", json={"items": [{"credits": "3", "grade_point": "6"}]})
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "no_grading_scheme"
    assert b"not configured" in sign_in(w, "s1@x.test").get("/academics/calculator").data


def test_students_cannot_change_official_results(w):
    result(w, "s1", "S1", "sem1", 40, "F", 0)
    s1 = sign_in(w, "s1@x.test")
    for method in ("post", "put", "patch", "delete"):
        for path in ("/api/student/results", "/academics/results"):
            assert getattr(s1, method)(path, json={"marks": 100, "grade_label": "O"}).status_code == 405
    with w["app"].app_context():
        assert query_one("SELECT grade_label, marks_obtained FROM academic_results")["grade_label"] == "F"


# ================================ library
def seed_books(w):
    for title, author, isbn, cat, total, avail in (("Alpha Programming", "Ann Writer", "978-1-11111-111-1", "Programming", 2, 1),
                                                    ("Beta Databases", "Bob Maker", "978-2-22222-222-2", "Databases", 1, 0),
                                                    ("100% Python_Tricks", "Cy Author", "978-3-33333-333-3", "Programming", 1, 1)):
        run(w, "INSERT INTO library_books (title, author, isbn, category, total_copies, available_copies) VALUES (%s, %s, %s, %s, %s, %s)", (title, author, isbn, cat, total, avail))


def test_library_search_filters_pagination_and_wildcards(w):
    seed_books(w)
    s1 = sign_in(w, "s1@x.test")
    titles = lambda q: [b["title"] for b in s1.get(f"/api/student/library/books?{q}").get_json()["data"]["items"]]
    assert titles("q=alpha") == ["Alpha Programming"] and titles("q=bob") == ["Beta Databases"]
    assert titles("q=9782222222222") == ["Beta Databases"] and titles("q=978-3-33333") == ["100% Python_Tricks"]
    assert titles("category=Programming") == ["100% Python_Tricks", "Alpha Programming"]
    assert titles("available=1") == ["100% Python_Tricks", "Alpha Programming"]
    assert titles("q=%25") == ["100% Python_Tricks"] and titles("q=_") == ["100% Python_Tricks"]       # wildcards are literal
    assert titles("q=%27%20OR%20%271%27%3D%271") == []                                                  # SQL injection attempt: just text
    assert titles("q=nonexistent") == []
    assert s1.get("/library/book/999999").status_code == 404
    assert s1.get("/library/?q=alpha").status_code == 200


def test_library_issues_are_private_and_show_overdue_and_fines(w):
    seed_books(w)
    b = query_all_one(w, "SELECT id FROM library_books WHERE title LIKE 'Alpha%'")["id"]
    run(w, "INSERT INTO library_issues (book_id, student_id, issued_on, due_on) VALUES (%s, %s, %s, %s)", (b, w["s1"], TODAY - datetime.timedelta(days=20), TODAY - datetime.timedelta(days=6)))
    run(w, "INSERT INTO library_issues (book_id, student_id, issued_on, due_on, returned_on, fine_amount) VALUES (%s, %s, %s, %s, %s, '12.50')",
        (b, w["s1"], TODAY - datetime.timedelta(days=90), TODAY - datetime.timedelta(days=76), TODAY - datetime.timedelta(days=70)))
    run(w, "INSERT INTO library_issues (book_id, student_id, issued_on, due_on) VALUES (%s, %s, %s, %s)", (b, w["s2"], TODAY, TODAY + datetime.timedelta(days=14)))
    mine = sign_in(w, "s1@x.test").get("/api/student/library/issues").get_json()["data"]
    assert [i["status"] for i in mine["current"]] == ["Overdue"] and mine["current"][0]["days_overdue"] == 6 and mine["overdue_count"] == 1
    assert mine["returned"][0]["fine"] == "12.50" and mine["fines_configured"] is False
    w["app"].config["LIBRARY_FINE_PER_DAY"] = "2.50"
    try:
        assert sign_in(w, "s1@x.test").get("/api/student/library/issues").get_json()["data"]["current"][0]["fine"] == "15.00"
    finally:
        w["app"].config["LIBRARY_FINE_PER_DAY"] = "0"
    theirs = sign_in(w, "s2@x.test").get("/api/student/library/issues").get_json()["data"]
    assert len(theirs["current"]) == 1 and theirs["current"][0]["status"] == "Issued" and theirs["returned"] == []
    s1 = sign_in(w, "s1@x.test")
    assert s1.post("/library/", json={}).status_code == 405 and s1.delete("/api/student/library/issues").status_code == 405
    with w["app"].app_context():
        assert query_one("SELECT available_copies FROM library_books WHERE id = %s", (b,))["available_copies"] == 1      # untouched


# ================================ syllabus and materials
def material(w, subject, title, stored, published=1, content=b"%PDF-1.4\nok\n%%EOF", write=True):
    if write:
        pdf(w["tmp"] / "materials", stored, content)
    return run(w, "INSERT INTO learning_materials (subject_id, title, original_filename, stored_filename, size_bytes, sha256, is_published) "
                  "VALUES (%s, %s, 'notes.pdf', %s, 20, %s, %s)", (w["subj"][subject], title, stored, "0" * 64, published))


def test_syllabus_is_filtered_by_department_and_semester(w):
    run(w, "INSERT INTO syllabus (subject_id, unit_number, title, topics) VALUES (%s, 1, 'Unit One', 'Topics')", (w["subj"]["S1"],))
    s1 = sign_in(w, "s1@x.test")
    sem1 = s1.get("/api/student/syllabus?semester=1").get_json()["data"]
    assert [s["code"] for s in sem1["subjects"]] == ["S1", "S2"] and sem1["subjects"][0]["units"][0]["title"] == "Unit One"
    assert [s["code"] for s in s1.get("/api/student/syllabus?semester=3").get_json()["data"]["subjects"]] == ["S3"]
    assert s1.get("/api/student/syllabus?semester=2").get_json()["data"]["subjects"] == []
    assert [s["code"] for s in sign_in(w, "s3@x.test").get("/api/student/syllabus?semester=1").get_json()["data"]["subjects"]] == ["C1"]
    assert s1.get("/syllabus/?semester=1").status_code == 200 and s1.get("/api/student/syllabus?semester=9").status_code == 422


def test_material_downloads_are_authorised_and_safe(w):
    ok = material(w, "S1", "Notes", "a" * 32 + ".pdf")
    future = material(w, "S5", "Later semester", "b" * 32 + ".pdf")
    other_dept = material(w, "C1", "CSE notes", "c" * 32 + ".pdf")
    draft = material(w, "S1", "Draft", "d" * 32 + ".pdf", published=0)
    traversal = material(w, "S1", "Evil", "../../secret.pdf", write=False)
    missing = material(w, "S1", "Gone", "e" * 32 + ".pdf", write=False)
    fake = material(w, "S1", "Not a pdf", "f" * 32 + ".pdf", content=b"MZ executable")
    s1 = sign_in(w, "s1@x.test")
    good = s1.get(f"/syllabus/materials/{ok}/download")
    assert good.status_code == 200 and good.mimetype == "application/pdf" and "attachment" in good.headers["Content-Disposition"]
    assert good.data.startswith(b"%PDF")
    for forbidden in (future, other_dept, draft, traversal, missing, fake, 999999):
        r = s1.get(f"/syllabus/materials/{forbidden}/download")
        assert r.status_code == 404 and str(w["tmp"]).encode() not in r.data and b"Traceback" not in r.data, forbidden
    assert sign_in(w, "s3@x.test").get(f"/syllabus/materials/{ok}/download").status_code == 404      # other department
    assert w["app"].test_client().get(f"/syllabus/materials/{ok}/download").status_code == 302      # anonymous
    assert sign_in(w, "t1@x.test").get(f"/syllabus/materials/{ok}/download").status_code == 200       # staff of the department
    assert s1.get("/syllabus/materials/abc/download").status_code == 404 and s1.get("/syllabus/materials/..%2f..%2fetc/download").status_code == 404


def test_students_cannot_upload_or_publish_materials(w):
    s1 = sign_in(w, "s1@x.test")
    for path in ("/syllabus/", "/syllabus/materials/1/download", "/api/student/syllabus"):
        assert s1.post(path, data={"file": (io.BytesIO(b"x"), "a.pdf")}).status_code in (404, 405)
    with w["app"].app_context():
        assert query_one("SELECT COUNT(*) AS c FROM learning_materials")["c"] == 0


# ================================ notices
def notice(w, title, **o):
    f = dict(category="general", audience="all", department_id=None, semester_number=None, active=1, published=days(-1),
             expires=None, pinned=0, attach=None)
    f.update(o)
    return run(w, "INSERT INTO notices (title, body, category, audience, department_id, semester_number, is_active, created_at, published_at, "
                  "expires_at, is_pinned, attachment_stored_name, attachment_original_name) VALUES (%s, 'Body', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
               (title, f["category"], f["audience"], f["department_id"], f["semester_number"], f["active"], f["published"], f["published"],
                f["expires"], f["pinned"], f["attach"], "rules.pdf" if f["attach"] else None))


def test_notice_visibility_rules(w):
    ids = {}
    ids["open"] = notice(w, "Open to all")
    ids["pinned"] = notice(w, "Pinned one", pinned=1, published=days(-5))
    ids["draft"] = notice(w, "Unpublished", active=0)
    ids["future"] = notice(w, "Scheduled", published=days(3))
    ids["cse"] = notice(w, "CSE only", department_id=w["cse"])
    ids["sem5"] = notice(w, "Semester 5 only", semester_number=5)
    ids["sem3"] = notice(w, "Semester 3 only", semester_number=3)
    ids["staff"] = notice(w, "Teachers only", audience="teacher")
    ids["expired"] = notice(w, "Expired", expires=days(-1))
    s1 = sign_in(w, "s1@x.test")
    shown = lambda c, q="": [n["title"] for n in c.get(f"/api/student/notices?{q}").get_json()["data"]["items"]]
    default = shown(s1)
    assert default[0] == "Pinned one" and set(default) == {"Pinned one", "Open to all", "Semester 3 only"}
    assert "Expired" in shown(s1, "include_expired=1") and "Expired" not in default
    cse_view = shown(sign_in(w, "s3@x.test"))                                   # a CSE student sees CSE-targeted notices
    assert cse_view[0] == "Pinned one" and set(cse_view) == {"Pinned one", "Open to all", "CSE only", "Semester 3 only"}
    for hidden in ("draft", "future", "cse", "sem5", "staff"):
        assert s1.get(f"/notices/{ids[hidden]}").status_code == 404, hidden
    assert s1.get(f"/notices/{ids['expired']}").status_code == 200                  # reachable by direct link
    assert shown(s1, "category=general&q=open") == ["Open to all"] and shown(s1, "q=nothing-here") == []
    assert s1.get("/notices/?category=exam&from=2026-01-01&expired=1").status_code == 200


def test_notice_attachments_and_write_protection(w):
    stored = "9" * 32 + ".pdf"
    pdf(w["tmp"] / "notices", stored)
    nid = notice(w, "With file", attach=stored)
    hidden = notice(w, "Hidden with file", attach=stored, active=0)
    s1 = sign_in(w, "s1@x.test")
    r = s1.get(f"/notices/{nid}/attachment")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert s1.get(f"/notices/{hidden}/attachment").status_code == 404
    assert s1.get(f"/notices/{notice(w, 'No file')}/attachment").status_code == 404
    run(w, "UPDATE notices SET attachment_stored_name = '../../x.pdf' WHERE id = %s", (nid,))
    assert s1.get(f"/notices/{nid}/attachment").status_code == 404
    for method in ("post", "put", "delete"):
        assert getattr(s1, method)("/notices/", json={"title": "Fake"}).status_code == 405
        assert getattr(s1, method)(f"/api/student/notices", json={"title": "Fake"}).status_code == 405
    with w["app"].app_context():
        assert query_one("SELECT COUNT(*) AS c FROM notices WHERE title = 'Fake'")["c"] == 0


# ================================ fees
def fee(w, student, name, due, payments, status="unpaid", remarks=None, due_days=-10):
    sid = run(w, "INSERT INTO fee_structures (name, department_id, academic_year_id, total_amount, due_date, category, semester_number) "
                 "VALUES (%s, %s, %s, %s, %s, 'Tuition', 3)", (name, w["aids"], w["ay"], due, TODAY + datetime.timedelta(days=due_days)))
    rid = run(w, "INSERT INTO student_fee_records (student_id, fee_structure_id, amount_due, due_date, status, remarks) VALUES (%s, %s, %s, %s, %s, %s)",
              (w[student], sid, due, TODAY + datetime.timedelta(days=due_days), status, remarks))
    for amount, tx_status in payments:
        run(w, "INSERT INTO fee_transactions (fee_record_id, amount, method, paid_on, status) VALUES (%s, %s, 'bank_transfer', %s, %s)", (rid, amount, TODAY, tx_status))
    return rid


def test_fee_balances_are_exact_and_only_confirmed_payments_count(w):
    fee(w, "s1", "Tuition", "1000.10", [("400.03", "confirmed"), ("0.07", "confirmed"), ("200.00", "pending"), ("300.00", "failed")])
    fee(w, "s1", "Exam", "3000.00", [("3000.00", "confirmed")], due_days=20)
    fee(w, "s1", "Waived fee", "500.00", [], status="waived")
    fee(w, "s1", "Not yet due", "250.00", [], due_days=30)
    data = sign_in(w, "s1@x.test").get("/api/student/fees").get_json()["data"]
    by = {r["fee_name"]: r for r in data["records"]}
    t = by["Tuition"]
    assert (t["confirmed_paid"], t["outstanding"], t["awaiting_confirmation"], t["status"]) == ("400.10", "600.00", "200.00", "Overdue")
    assert by["Exam"]["status"] == "Paid" and by["Exam"]["outstanding"] == "0.00"
    assert by["Waived fee"]["status"] == "Waived" and by["Waived fee"]["outstanding"] == "0.00"
    assert by["Not yet due"]["status"] == "Pending"
    assert data["totals"]["assessed"] == "4750.10" and data["totals"]["confirmed"] == "3400.10" and data["totals"]["outstanding"] == "850.00"
    assert len(t["transactions"]) == 4                                           # pending/failed are listed...
    page = sign_in(w, "s1@x.test").get("/fees/")
    assert b"600.00" in page.data and b"Pay now" not in page.data and b"payment gateway" not in page.data.lower()


def test_fee_records_are_private_and_read_only(w):
    fee(w, "s1", "Tuition", "1000.00", [("100.00", "confirmed")])
    s2 = sign_in(w, "s2@x.test")
    assert s2.get("/api/student/fees").get_json()["data"]["records"] == [] and b"No fee records" in s2.get("/fees/").data
    s1 = sign_in(w, "s1@x.test")
    for method in ("post", "put", "patch", "delete"):
        assert getattr(s1, method)("/api/student/fees", json={"amount_due": "0.00", "status": "paid"}).status_code == 405
        assert getattr(s1, method)("/fees/", json={"amount_due": "0.00"}).status_code == 405
    with w["app"].app_context():
        row = query_one("SELECT amount_due, status FROM student_fee_records")
    assert row["amount_due"] == D("1000.00") and row["status"] == "unpaid"


# ================================ profile and password
def test_profile_shows_own_data_and_updates_only_the_phone(w):
    s1 = sign_in(w, "s1@x.test")
    assert b"R-s1" in s1.get("/student/profile").data
    r = s1.post("/student/profile/contact", data={"phone": "+91 98765 43210", "full_name": "Hacker", "roll_number": "HACK-1",
                                                    "role": "admin", "department_id": str(w["cse"]), "status": "suspended", "email": "evil@x.test"})
    assert r.status_code == 302
    with w["app"].app_context():
        user = query_one("SELECT full_name, role, status, email FROM users WHERE id = %s", (w["s1"],))
        prof = query_one("SELECT roll_number, department_id, phone FROM student_profiles WHERE user_id = %s", (w["s1"],))
    assert (user["full_name"], user["role"], user["status"], user["email"]) == ("User s1", "student", "active", "s1@x.test")
    assert (prof["roll_number"], prof["department_id"], prof["phone"]) == ("R-s1", w["aids"], "+91 98765 43210")
    assert s1.post("/student/profile/contact", data={"phone": "<script>"}).status_code == 422
    assert s1.get("/student/").status_code == 200                                    # still a student, still active
    assert sign_in(w, "t1@x.test").get("/student/profile").status_code == 403
    assert w["app"].test_client().get("/student/profile").status_code == 302


def test_password_change_uses_the_existing_auth_rules(w):
    s1 = sign_in(w, "s1@x.test")
    post = lambda **d: s1.post("/student/profile/password", data=d)
    assert post(current_password="wrong-pass1", new_password="NewPass123", confirm_password="NewPass123").status_code == 400
    assert post(current_password=PASSWORD, new_password="short1", confirm_password="short1").status_code == 400
    assert post(current_password=PASSWORD, new_password="NoDigitsHere", confirm_password="NoDigitsHere").status_code == 400
    assert post(current_password=PASSWORD, new_password="NewPass123", confirm_password="Different123").status_code == 400
    assert post(current_password=PASSWORD, new_password=PASSWORD, confirm_password=PASSWORD).status_code == 400    # must differ
    assert post(current_password=PASSWORD, new_password="NewPass123", confirm_password="NewPass123").status_code == 302
    assert s1.get("/student/").status_code == 200                                    # still signed in
    with w["app"].app_context():
        h = query_one("SELECT password_hash FROM users WHERE id = %s", (w["s1"],))["password_hash"]
        assert h.startswith("pbkdf2:sha256:600000") and "NewPass123" not in h
        assert query_one("SELECT COUNT(*) AS c FROM audit_logs WHERE action = 'password.change'")["c"] == 1
    fresh = w["app"].test_client(); fresh.get("/login")
    assert fresh.post("/login", data={"email": "s1@x.test", "password": PASSWORD}).status_code == 401          # old password dead
    assert fresh.post("/login", data={"email": "s1@x.test", "password": "NewPass123"}).status_code == 302


def test_repeated_wrong_current_password_hits_the_login_lockout(w):
    s1 = sign_in(w, "s1@x.test")
    for _ in range(5):
        assert s1.post("/student/profile/password", data={"current_password": "bad", "new_password": "NewPass123", "confirm_password": "NewPass123"}).status_code == 400
    r = s1.post("/student/profile/password", data={"current_password": PASSWORD, "new_password": "NewPass123", "confirm_password": "NewPass123"})
    assert r.status_code == 400 and b"Too many failed attempts" in r.data


# ================================ migrations
def test_migration_002_columns_and_constraints_exist(w):
    with w["app"].app_context():
        cols = {r["COLUMN_NAME"] for r in query_all(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN "
            "('notices', 'fee_transactions', 'student_fee_records', 'grading_scheme_rules', 'library_books', 'learning_materials')")}
        for needed in ("is_pass", "publisher", "is_published", "is_pinned", "expires_at", "attachment_stored_name", "remarks", "status"):
            assert needed in cols, needed
        applied = {r["version"] for r in query_all("SELECT version FROM schema_migrations")}
        assert {"001_phase3_attendance", "002_phase4_student_portal"} <= applied
