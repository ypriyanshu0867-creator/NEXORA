"""PHASE 5 (teacher side) UNIT TESTS - no MySQL needed.

Marks/grade rules, upload validation, form validation, route protection and read-only
guarantees. They do NOT prove any SQL works; see tests/test_phase5_integration.py.
"""
import datetime
import io
import re
from decimal import Decimal as D

import pytest
from werkzeug.datastructures import FileStorage

from app import clock
from app.services import content_service, correction_service, result_service, teacher_notice_service, upload_service
from app.services.errors import ServiceError

RULES = [{"grade_label": "O", "min_percent": D("90"), "max_percent": D("100"), "grade_point": D("10")},
         {"grade_label": "A", "min_percent": D("70"), "max_percent": D("89.99"), "grade_point": D("8")},
         {"grade_label": "B", "min_percent": D("50"), "max_percent": D("69.99"), "grade_point": D("6")},
         {"grade_label": "F", "min_percent": D("0"), "max_percent": D("49.99"), "grade_point": D("0")}]


# ---------------------------------------------------------- marks and grades
def test_grade_is_derived_from_the_scheme_bands():
    pick = lambda pct: result_service.grade_for_percent(RULES, D(str(pct)))["grade_label"]
    assert [pick(p) for p in (100, 90, 89.99, 89.995, 70, 69.99, 50, 49.99, 0)] == ["O", "O", "A", "A", "A", "B", "B", "F", "F"]
    assert result_service.grade_for_percent(RULES, D("-1")) is None
    assert result_service.grade_for_percent(list(reversed(RULES)), D("75"))["grade_label"] == "A"      # order of rules does not matter


def test_marks_validation():
    parse = result_service.parse_marks
    assert parse("85", "100") == (D("85"), D("100")) and parse("42.5", "50") == (D("42.5"), D("50"))
    assert parse("", "100") == (None, None) and parse(None, None) == (None, None) and parse("  ", "100") == (None, None)
    assert parse("70", "") == (D("70"), D("100"))                                                       # default maximum
    for bad in (("101", "100"), ("-1", "100"), ("abc", "100"), ("NaN", "100"), ("Infinity", "100"), ("5", "0"), ("5", "-10"),
                ("5", "1001"), ("1.234", "100"), ("5", "xx")):
        assert isinstance(parse(*bad), str), bad


def test_publish_blocked_when_the_institution_reserves_it(unit_app):
    unit_app.config["TEACHER_CAN_PUBLISH_RESULTS"] = False
    with unit_app.app_context(), pytest.raises(ServiceError) as info:
        result_service.save_results(1, 1, [], True, "ip")
    assert info.value.code == "publish_not_allowed" and info.value.status == 403


# ------------------------------------------------------------------- uploads
def upload(content, name="notes.pdf"):
    return FileStorage(stream=io.BytesIO(content), filename=name)


def test_upload_accepts_a_real_pdf_and_stores_it_under_a_random_name(unit_app, tmp_path):
    unit_app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with unit_app.app_context():
        saved = upload_service.save_pdf(upload(b"%PDF-1.4\nhello\n%%EOF", "../../My Notes.PDF"), "materials")
    stored = tmp_path / "materials" / saved["stored_name"]
    assert stored.read_bytes().startswith(b"%PDF-") and len(saved["stored_name"]) == 36 and saved["stored_name"].endswith(".pdf")
    assert saved["original_name"] == "My_Notes.PDF" and saved["size"] == 20 and len(saved["sha256"]) == 64
    assert not list((tmp_path / "materials").glob("*.part"))                                            # temp file was renamed


def test_upload_rejections(unit_app, tmp_path):
    unit_app.config["UPLOAD_FOLDER"] = str(tmp_path)
    unit_app.config["MATERIAL_MAX_BYTES"] = 100
    cases = [(upload(b"%PDF-1.4 ok " + b"x" * 200), "file_too_large"), (upload(b"MZ\x90\x00 program.exe contents"), "invalid_pdf"),
             (upload(b"%PDF-1.4 ok again", "virus.exe"), "invalid_file_type"), (upload(b"%PDF-1.4 ok again", "a.pdf.exe"), "invalid_file_type"),
             (upload(b"%PDF", "tiny.pdf"), "invalid_pdf"), (upload(b"", ""), "file_required"), (None, "file_required")]
    with unit_app.app_context():
        for file, code in cases:
            with pytest.raises(ServiceError) as info:
                upload_service.save_pdf(file, "materials")
            assert info.value.code == code, code
    assert not any((tmp_path / "materials").glob("*")) if (tmp_path / "materials").exists() else True     # nothing was written


def test_remove_file_only_touches_its_own_folder(unit_app, tmp_path):
    unit_app.config["UPLOAD_FOLDER"] = str(tmp_path)
    (tmp_path / "materials").mkdir()
    victim = tmp_path / "keep.txt"
    victim.write_text("keep")
    with unit_app.app_context():
        upload_service.remove_file("materials", "../keep.txt")
    assert victim.exists()


# ------------------------------------------------------ form validation (pure)
def test_syllabus_unit_validation():
    ok = content_service._clean_unit({"unit_number": "2", "title": "Trees", "topics": "BST", "lecture_hours": "8", "is_published": "1"})
    assert ok["unit_number"] == 2 and ok["lecture_hours"] == 8 and ok["is_published"] is True and ok["learning_outcomes"] is None
    for bad, field in (({"unit_number": "0", "title": "t", "topics": "x"}, "unit_number"), ({"unit_number": "31", "title": "t", "topics": "x"}, "unit_number"),
                       ({"unit_number": "a", "title": "t", "topics": "x"}, "unit_number"), ({"unit_number": "1", "title": "", "topics": "x"}, "title"),
                       ({"unit_number": "1", "title": "t", "topics": ""}, "topics"), ({"unit_number": "1", "title": "t" * 151, "topics": "x"}, "title"),
                       ({"unit_number": "1", "title": "t", "topics": "x", "lecture_hours": "300"}, "lecture_hours")):
        with pytest.raises(ServiceError) as info:
            content_service._clean_unit(bad)
        assert field in info.value.fields, bad


def test_correction_reason_and_choice_are_validated(unit_app):
    with unit_app.app_context():
        for reason, present, field in (("", True, "reason"), ("abc", True, "reason"), ("x" * 256, False, "reason"),
                                       ("Valid reason here", None, "present"), ("Valid reason here", "yes", "present")):
            with pytest.raises(ServiceError) as info:
                correction_service.apply_correction(1, 1, 1, present, reason, "ip")
            assert info.value.code == "validation_error" and field in info.value.fields, (reason, present)


def test_teacher_notice_categories_exclude_administrative_ones():
    assert set(teacher_notice_service.TEACHER_CATEGORIES) == {"general", "exam", "assignment", "timetable", "material"}
    assert not {"holiday", "event", "fee", "library", "result"} & set(teacher_notice_service.TEACHER_CATEGORIES)


def test_notice_date_parsing():
    errors = {}
    assert teacher_notice_service._when("2026-12-01T09:30", "d", errors) == datetime.datetime(2026, 12, 1, 9, 30)
    assert teacher_notice_service._when("2026-12-01", "e", errors, end_of_day=True) == datetime.datetime(2026, 12, 1, 23, 59, 59)
    assert teacher_notice_service._when("", "d", errors) is None and errors == {}
    assert teacher_notice_service._when("01/12/2026", "bad", errors) is None and "bad" in errors


def test_notice_validation_runs_before_any_scope_is_granted(unit_app):
    """With no assignments, nothing is a valid class: a teacher cannot address an arbitrary class id."""
    from unittest import mock
    with unit_app.app_context(), mock.patch.object(teacher_notice_service, "scope_options", return_value=[]):
        with pytest.raises(ServiceError) as info:
            teacher_notice_service.clean(1, {"title": "Hello there", "body": "Body", "category": "fee", "class_id": "7"})
    assert {"category", "class_id"} <= set(info.value.fields)                    # fee category and foreign class both rejected


def test_notice_expiry_must_be_in_the_future(unit_app):
    from unittest import mock
    scope = [{"class_id": 3, "label": "SE A", "semesters": [3]}]
    base = {"title": "Hello there", "body": "Body", "category": "general", "class_id": "3"}
    with unit_app.app_context(), mock.patch.object(teacher_notice_service, "scope_options", return_value=scope):
        good = teacher_notice_service.clean(1, {**base, "semester_number": "3", "expires_at": "2999-01-01"})
        assert good["class_id"] == 3 and good["semester_number"] == 3 and good["expires_at"].year == 2999
        for extra, field in (({"expires_at": "2000-01-01"}, "expires_at"), ({"semester_number": "5"}, "semester_number"),
                             ({"class_id": "4"}, "class_id")):
            with pytest.raises(ServiceError) as info:
                teacher_notice_service.clean(1, {**base, **extra})
            assert field in info.value.fields, extra


# ------------------------------------------------------- HTTP-level (no DB)
TEACHER_PAGES = ("/teacher/", "/teacher/classes", "/teacher/classes/1", "/teacher/sessions", "/teacher/sessions/1", "/teacher/reports",
                 "/teacher/results", "/teacher/results/1", "/teacher/results/1/export", "/teacher/syllabus", "/teacher/syllabus/1",
                 "/teacher/notices", "/teacher/notices/new", "/teacher/notices/1/edit")


def test_teacher_pages_require_login(unit_app):
    client = unit_app.test_client()
    for path in TEACHER_PAGES:
        response = client.get(path)
        assert response.status_code == 302 and "/login" in response.headers["Location"], path


def test_teacher_post_routes_exist_only_as_posts_and_are_teacher_only(unit_app):
    rules = {}
    for r in unit_app.url_map.iter_rules():
        if r.rule.startswith("/teacher"):
            rules.setdefault(r.rule, set()).update(r.methods)
    posts = {rule for rule, methods in rules.items() if "POST" in methods}
    assert {"/teacher/sessions/<int:session_id>/correct", "/teacher/results/<int:assignment_id>",
            "/teacher/syllabus/<int:subject_id>/units", "/teacher/syllabus/<int:subject_id>/materials",
            "/teacher/notices/<int:notice_id>/publish"} <= posts
    # state-changing routes never answer GET
    for rule in ("/teacher/sessions/<int:session_id>/correct", "/teacher/syllabus/<int:subject_id>/units",
                 "/teacher/syllabus/<int:subject_id>/materials", "/teacher/notices/<int:notice_id>/publish",
                 "/teacher/syllabus/units/<int:unit_id>/publish", "/teacher/syllabus/materials/<int:material_id>/publish"):
        assert "GET" not in rules[rule], rule


def test_no_route_lets_a_teacher_choose_assignments_or_roles(unit_app):
    """Teachers have no endpoint that writes teacher_subjects, users.role or department assignments."""
    forbidden = ("assign", "role", "department", "teacher_subjects")
    for rule in unit_app.url_map.iter_rules():
        if rule.rule.startswith(("/teacher", "/api/teacher")) and rule.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            fixed_path = re.sub(r"<[^>]+>", "", rule.rule)              # ignore parameter names such as <assignment_id>
            assert not any(word in fixed_path for word in forbidden), rule.rule


def test_teacher_navigation_links_are_live(unit_app):
    from app.navigation import build_nav
    with unit_app.test_request_context("/teacher/"):
        items = {i["label"]: i for i in build_nav("teacher")}
    assert all(i["available"] for i in items.values()), [k for k, v in items.items() if not v["available"]]
    assert {"My Classes", "Session History", "Attendance Reports", "Results", "Syllabus & Materials", "Notices"} <= set(items)


def test_audit_and_correction_triggers_are_documented_but_not_required_by_the_migration():
    import pathlib
    migration = pathlib.Path("database/migrations/003_phase5_teacher_tools.sql").read_text(encoding="utf8")
    assert "CREATE TRIGGER" not in migration and "protect_audit_tables.py" in migration       # needs admin rights, so it is a separate script
    script = pathlib.Path("scripts/protect_audit_tables.py").read_text(encoding="utf8")
    assert "trg_audit_logs_no_update" in script and "trg_corrections_no_delete" in script
