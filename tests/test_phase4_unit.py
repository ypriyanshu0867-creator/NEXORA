"""PHASE 4 UNIT TESTS - no MySQL needed.

Pure grading/fee/library rules, safe file serving, notice visibility rules,
and calculator validation (the database lookups are replaced by mocks and
are labelled as such). These do NOT prove any SQL works; see
tests/test_phase4_integration.py for the MySQL-backed checks.
"""
import datetime
import os
from decimal import Decimal as D
from unittest import mock

import pytest

from app.api import to_jsonable
from app.services import academic_service as acad
from app.services import fee_service, file_service, library_service, notice_service
from app.services.errors import ServiceError
from app.template_filters import gpa, money

T = datetime.date


def row(**o):
    base = {"id": 1, "subject_id": 1, "code": "S1", "credits": D("4"), "grade_point": D("10"), "grade_label": "O",
            "semester_start": T(2025, 6, 1), "entered_at": datetime.datetime(2026, 1, 1), "semester_name": "Sem 1",
            "name": "Subject", "semester_id": 1, "year_label": "2025-26"}
    base.update(o)
    return base


# ------------------------------------------------------------ GPA maths
def test_gpa_is_credit_weighted_and_exact():
    result = acad.compute_gpa([row(credits=D("4"), grade_point=D("9")), row(credits=D("3"), grade_point=D("8")),
                               row(credits=D("2"), grade_point=D("10"))])
    assert result["total_credits"] == D("9") and result["total_weighted"] == D("80")
    assert result["gpa"] == D("8.89")                                       # 80/9 = 8.888...


def test_rounding_is_half_up_not_floating_point():
    assert acad.round_gpa(D("8.125"), 2) == D("8.13")                      # float formatting gives 8.12
    assert acad.round_gpa(D("8.125"), 1) == D("8.1") and acad.round_gpa(D("8.15"), 1) == D("8.2")
    assert acad.round_gpa(D("7"), 3) == D("7.000")


def test_zero_credit_subjects_carry_no_weight_and_empty_gives_none():
    assert acad.compute_gpa([row(credits=D("0"), grade_point=D("10")), row(credits=D("2"), grade_point=D("6"))])["gpa"] == D("6.00")
    assert acad.compute_gpa([])["gpa"] is None
    assert acad.compute_gpa([row(credits=D("0"))])["gpa"] is None


def test_cgpa_is_not_the_average_of_sgpas():
    sem1 = [row(credits=D("2"), grade_point=D("10"))]                       # SGPA 10 over 2 credits
    sem2 = [row(subject_id=2, credits=D("8"), grade_point=D("6"))]          # SGPA 6 over 8 credits
    assert acad.compute_gpa(sem1 + sem2)["gpa"] == D("6.80")                # a simple average would say 8.00


def test_repeat_policy_latest_and_best():
    fail = row(id=1, grade_point=D("0"), semester_start=T(2025, 6, 1))
    retake = row(id=2, grade_point=D("6"), semester_start=T(2026, 6, 1))
    other = row(id=3, subject_id=2, code="S2", grade_point=D("8"))
    counted, superseded = acad.apply_repeat_policy([fail, retake, other], "latest")
    assert {r["id"] for r in counted} == {2, 3} and [r["id"] for r in superseded] == [1]
    worse_retake = row(id=4, grade_point=D("4"), semester_start=T(2026, 6, 1))
    assert {r["id"] for r in acad.apply_repeat_policy([retake, worse_retake], "latest")[0]} == {4}
    assert {r["id"] for r in acad.apply_repeat_policy([retake, worse_retake], "best")[0]} == {2}


def test_result_status_uses_scheme_flag_then_falls_back_to_grade_points():
    assert acad.result_status({"is_pass": 1, "grade_point": D("0")}) == "Pass"
    assert acad.result_status({"is_pass": 0, "grade_point": D("5")}) == "Fail"
    assert acad.result_status({"is_pass": None, "grade_point": D("0")}) == "Fail"
    assert acad.result_status({"is_pass": None, "grade_point": D("4")}) == "Pass"


# ------------------------------------------- calculators (DB lookups MOCKED)
SCHEME = {"id": 1, "name": "Demo scheme", "rules": [
    {"grade_label": "O", "min_percent": D("90"), "max_percent": D("100"), "grade_point": D("10"), "is_pass": 1},
    {"grade_label": "B", "min_percent": D("50"), "max_percent": D("89.99"), "grade_point": D("6"), "is_pass": 1},
    {"grade_label": "F", "min_percent": D("0"), "max_percent": D("49.99"), "grade_point": D("0"), "is_pass": 0}]}
CTX = {"student_id": 7, "department_id": 1}


def manual(items, fn=acad.calculate_sgpa, app=None):
    with app.app_context(), mock.patch.object(acad, "default_scheme", return_value=SCHEME):
        return fn(CTX, {"items": items})


def field_errors(app, items, fn=acad.calculate_sgpa):
    with pytest.raises(ServiceError) as info:
        manual(items, fn, app)
    return info.value.fields or {}, info.value.code


def test_manual_sgpa_calculation_and_estimate_label(unit_app):
    out = manual([{"name": "A", "credits": "4", "grade_point": "10"}, {"name": "B", "credits": 3, "grade_point": 6}], app=unit_app)
    assert out["gpa"] == D("8.29") and out["total_credits"] == D("7") and out["total_weighted"] == D("58")
    assert out["is_estimate"] is True and "Estimate" in out["label"]


def test_manual_calculator_rejects_bad_credits_and_grade_points(unit_app):
    bad = [({"credits": "-1", "grade_point": "6"}, "items.0.credits"), ({"credits": "0", "grade_point": "6"}, "items.0.credits"),
           ({"credits": "31", "grade_point": "6"}, "items.0.credits"), ({"credits": "abc", "grade_point": "6"}, "items.0.credits"),
           ({"credits": "NaN", "grade_point": "6"}, "items.0.credits"), ({"credits": True, "grade_point": "6"}, "items.0.credits"),
           ({"credits": "3", "grade_point": "11"}, "items.0.grade_point"), ({"credits": "3", "grade_point": "7"}, "items.0.grade_point"),
           ({"credits": "3", "grade_point": "-1"}, "items.0.grade_point"), ({"credits": "3", "grade_point": "Infinity"}, "items.0.grade_point"),
           ({"credits": "3"}, "items.0.grade_point")]
    for item, key in bad:
        fields, code = field_errors(unit_app, [item])
        assert code == "validation_error" and key in fields, item


def test_calculator_limits_and_empty_input(unit_app):
    assert field_errors(unit_app, [])[1] == "validation_error"
    assert field_errors(unit_app, [{"credits": "1", "grade_point": "6"}] * 41)[1] == "validation_error"


def test_missing_grading_scheme_is_explained(unit_app):
    with unit_app.app_context(), mock.patch.object(acad, "default_scheme", return_value=None):
        for fn in (acad.calculate_sgpa, acad.calculate_cgpa):
            with pytest.raises(ServiceError) as info:
                fn(CTX, {"items": [{"credits": "3", "grade_point": "6"}]})
            assert info.value.code == "no_grading_scheme" and "institution" in info.value.message


def sem_subjects():
    return {1: {"id": 1, "code": "S1", "name": "One", "credits": D("4")}, 2: {"id": 2, "code": "S2", "name": "Two", "credits": D("3")}}


def subject_mode(app, grades, official=()):
    with app.app_context(), mock.patch.object(acad, "default_scheme", return_value=SCHEME), \
            mock.patch.object(acad, "_student_subjects", return_value=sem_subjects()), \
            mock.patch.object(acad, "_published_rows", return_value=list(official)):
        return acad.calculate_sgpa(CTX, {"semester_number": 1, "grades": grades})


def test_subject_mode_excludes_ungraded_subjects_and_says_so(unit_app):
    out = subject_mode(unit_app, {"1": "O"})
    assert out["gpa"] == D("10.00") and out["total_credits"] == D("4")
    assert out["not_included"] == [{"code": "S2", "name": "Two", "reason": "No grade selected"}]


def test_subject_mode_matches_official_only_when_identical(unit_app):
    official = [row(subject_id=1, grade_label="O", semester_number=1), row(subject_id=2, grade_label="B", semester_number=1)]
    same = subject_mode(unit_app, {"1": "O", "2": "B"}, official)
    assert same["is_estimate"] is False and "official" in same["label"]
    assert subject_mode(unit_app, {"1": "O", "2": "O"}, official)["is_estimate"] is True
    assert subject_mode(unit_app, {"1": "O"}, official)["is_estimate"] is True       # a subject was left out


def test_subject_mode_rejects_unknown_grade_and_foreign_subject(unit_app):
    for grades, key in (({"1": "Z"}, "grades.1"), ({"1": "O", "99": "O"}, "grades")):
        with pytest.raises(ServiceError) as info:
            subject_mode(unit_app, grades)
        assert key in info.value.fields
    with pytest.raises(ServiceError) as info:
        subject_mode(unit_app, {})
    assert info.value.code == "no_grades"


def test_cgpa_with_hypothetical_semester_applies_repeat_policy_and_is_estimate(unit_app):
    official = [row(id=1, subject_id=1, code="S1", credits=D("4"), grade_point=D("0"), grade_label="F", semester_number=1)]
    with unit_app.app_context(), mock.patch.object(acad, "default_scheme", return_value=SCHEME), \
            mock.patch.object(acad, "_student_subjects", return_value=sem_subjects()), \
            mock.patch.object(acad, "_published_rows", return_value=official):
        out = acad.calculate_cgpa(CTX, {"hypothetical": {"semester_number": 3, "grades": {"1": "B", "2": "O"}}})
    assert out["gpa"] == D("7.71")                           # S1 retaken (6) + S2 (10): (24+30)/7 ; failed attempt not counted
    assert out["is_estimate"] is True and [s["code"] for s in out["superseded"]] == ["S1"]


def test_cgpa_without_any_data_is_explained(unit_app):
    with unit_app.app_context(), mock.patch.object(acad, "default_scheme", return_value=SCHEME), \
            mock.patch.object(acad, "_published_rows", return_value=[]):
        with pytest.raises(ServiceError) as info:
            acad.calculate_cgpa(CTX, {})
    assert info.value.code == "no_data"


# ---------------------------------------------------------------------- fees
def test_fee_status_rules_and_exact_decimal_balances():
    today = T(2026, 10, 28)
    past, future = T(2026, 10, 1), T(2026, 11, 30)
    assert fee_service.fee_status(D("1000.10"), D("1000.10"), past, today, False) == ("Paid", D("0.00"))
    assert fee_service.fee_status(D("1000.10"), D("400.10"), future, today, False) == ("Partially paid", D("600.00"))
    assert fee_service.fee_status(D("1000.10"), D("400.10"), past, today, False) == ("Overdue", D("600.00"))
    assert fee_service.fee_status(D("1000.10"), D("0.00"), future, today, False) == ("Pending", D("1000.10"))
    assert fee_service.fee_status(D("1000.10"), D("0.00"), past, today, False)[0] == "Overdue"
    assert fee_service.fee_status(D("1000.10"), D("0.00"), past, today, True) == ("Waived", D("0.00"))
    assert fee_service.fee_status(D("100"), D("150"), past, today, False) == ("Paid", D("0.00"))   # over-payment
    assert (D("0.1") + D("0.2")) == D("0.3")                                   # why Decimal, not float


def test_money_and_gpa_filters_never_use_floats():
    assert money(D("1234567.8")) == "1,234,567.80" and money(D("0.005")) == "0.01" and money(None) == "-"
    assert gpa(D("8.125")) == "8.13" and gpa(None) == "-" and gpa(D("7"), 3) == "7.000"


# ------------------------------------------------------------------- library
def test_library_overdue_and_fine_rules():
    today = T(2026, 10, 28)
    issued = {"issued_on": T(2026, 10, 1), "due_on": T(2026, 10, 15), "returned_on": None, "fine_amount": D("0.00")}
    assert library_service.classify_issue(issued, today, D("0")) == {"status": "Overdue", "days_overdue": 13, "fine": D("0.00")}
    assert library_service.classify_issue(issued, today, D("2.50"))["fine"] == D("32.50")
    ok = dict(issued, due_on=T(2026, 11, 5))
    assert library_service.classify_issue(ok, today, D("2.50")) == {"status": "Issued", "days_overdue": 0, "fine": D("0.00")}
    late = dict(issued, returned_on=T(2026, 10, 20), fine_amount=D("25.00"))
    assert library_service.classify_issue(late, today, D("2.50")) == {"status": "Returned", "days_overdue": 5, "fine": D("25.00")}
    early = dict(issued, returned_on=T(2026, 10, 10))
    assert library_service.classify_issue(early, today, D("2.50"))["days_overdue"] == 0


def test_like_escaping_neutralises_wildcards():
    assert library_service._like("100%_off\\") == "%100\\%\\_off\\\\%"


# ------------------------------------------------------------------- notices
NOW = datetime.datetime(2026, 10, 28, 12, 0)


def test_student_notice_filter_targets_department_semester_and_audience():
    clause, params = notice_service._audience_clause(
        {"role": "student", "department_id": 5, "semester_number": 3}, NOW, include_expired=False)
    assert "n.is_active = 1" in clause and "n.audience IN ('all', 'student')" in clause
    assert "n.department_id IS NULL OR n.department_id = %s" in clause and "n.semester_number IS NULL OR n.semester_number = %s" in clause
    assert "n.expires_at IS NULL OR n.expires_at > %s" in clause
    assert "n.class_id IS NULL OR n.class_id = %s" in clause
    assert params == [NOW, 5, 3, 0, NOW]                                # class id 0 = student has no class


def test_expired_notices_only_when_requested_and_staff_audiences_differ():
    clause, params = notice_service._audience_clause({"role": "student", "department_id": 1, "semester_number": 1}, NOW, True)
    assert "expires_at" not in clause and len(params) == 4          # now, department, semester, class
    teacher, _ = notice_service._audience_clause({"role": "teacher", "department_id": 1}, NOW, False)
    assert "'all', 'teacher'" in teacher and "'student'" not in teacher


def test_notice_categories_cover_the_required_set():
    assert {"general", "exam", "timetable", "holiday", "event", "assignment", "result", "fee", "library",
            "material"} == notice_service.CATEGORY_KEYS


# ------------------------------------------------------------- safe file serving
def make_pdf(folder, name, content=b"%PDF-1.4\nhello\n%%EOF"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(content)


def test_stored_file_resolution_accepts_only_server_generated_names(unit_app, tmp_path):
    good = "0123456789abcdef0123456789abcdef.pdf"
    make_pdf(tmp_path / "materials", good)
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    unit_app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with unit_app.app_context():
        assert file_service.resolve_stored_file("materials", good).name == good
        for bad in ("../secret.pdf", "..\\secret.pdf", "/etc/passwd", "C:\\Windows\\win.ini", "a/b.pdf", "%2e%2e%2fsecret.pdf",
                    "0123456789abcdef0123456789abcdef.exe", "0123456789ABCDEF0123456789ABCDEF.pdf", "short.pdf", "",
                    None, 5, good + "\x00", " " + good, good + "/"):
            with pytest.raises(ServiceError) as info:
                file_service.resolve_stored_file("materials", bad)
            assert info.value.status == 404, repr(bad)


def test_missing_and_non_pdf_files_are_refused(unit_app, tmp_path):
    unit_app.config["UPLOAD_FOLDER"] = str(tmp_path)
    make_pdf(tmp_path / "materials", "11111111111111111111111111111111.pdf", b"MZ\x90\x00 not a pdf")
    with unit_app.app_context():
        for name in ("22222222222222222222222222222222.pdf", "11111111111111111111111111111111.pdf"):
            with pytest.raises(ServiceError) as info:
                file_service.resolve_stored_file("materials", name)
            assert info.value.code == "file_not_found" and str(tmp_path) not in info.value.message     # no path leak


def test_symlink_escape_is_blocked(unit_app, tmp_path):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    folder = tmp_path / "uploads" / "materials"
    folder.mkdir(parents=True)
    link = folder / "33333333333333333333333333333333.pdf"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this system")
    unit_app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    with unit_app.app_context(), pytest.raises(ServiceError):
        file_service.resolve_stored_file("materials", link.name)


def test_download_name_is_sanitised():
    assert file_service.download_name("../../evil name.pdf", "x.pdf") == "evil_name.pdf"
    assert file_service.download_name("notes", "x.pdf") == "notes.pdf" and file_service.download_name("", "fallback.pdf") == "fallback.pdf"


# ------------------------------------------------------------------ JSON
def test_json_conversion_keeps_decimals_exact_and_dates_iso():
    out = to_jsonable({"a": D("600.00"), "b": datetime.date(2026, 1, 2), "c": [datetime.datetime(2026, 1, 2, 3, 4, 5)], "d": 1})
    assert out == {"a": "600.00", "b": "2026-01-02", "c": ["2026-01-02T03:04:05Z"], "d": 1}


# ----------------------------------------------------- HTTP checks (no DB used)
def test_student_pages_and_api_require_login(unit_app):
    client = unit_app.test_client()
    for path in ("/academics/results", "/academics/calculator", "/library/", "/library/my-books", "/syllabus/", "/notices/",
                 "/notices/1", "/fees/", "/student/profile", "/syllabus/materials/1/download", "/notices/1/attachment"):
        response = client.get(path)
        assert response.status_code == 302 and "/login" in response.headers["Location"], path
    for path in ("/api/student/dashboard", "/api/student/results", "/api/student/fees", "/api/student/notices",
                 "/api/student/library/issues", "/api/student/syllabus", "/api/student/calculator-data"):
        response = client.get(path)
        assert response.status_code == 401 and response.get_json()["error"]["code"] == "authentication_required", path


def test_students_cannot_write_through_read_only_endpoints(unit_app):
    """Only GET routes exist for results, fees, library and notices; writes are 405 (or 401 anonymously)."""
    rules = {r.rule: r.methods for r in unit_app.url_map.iter_rules()}
    for rule in ("/academics/results", "/fees/", "/library/", "/library/my-books", "/notices/", "/notices/<int:notice_id>",
                 "/api/student/results", "/api/student/fees", "/api/student/library/issues", "/api/student/notices",
                 "/api/student/library/books", "/syllabus/"):
        assert not (rules[rule] & {"POST", "PUT", "PATCH", "DELETE"}), rule
    writers = sorted(rule for rule, methods in rules.items() if methods & {"POST", "PUT", "PATCH", "DELETE"} and
                     rule.startswith(("/api/student", "/student")))
    assert writers == ["/api/student/cgpa/calculate", "/api/student/sgpa/calculate", "/student/profile/contact",
                       "/student/profile/password"]        # the ONLY student-facing writes: calculators (no DB write) and profile
