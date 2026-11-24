r"""PHASE 5 (teacher side) INTEGRATION TESTS - require a real MySQL test database.

Prepare once:   python scripts\init_db.py --test        (applies migrations 001 to 003)
Optional:       python scripts\protect_audit_tables.py --test   (enables the append-only trigger test)
Run:            python -m pytest tests -v
Skipped automatically when MySQL is unreachable. The test database is emptied before every test.
"""
import datetime
import io
import json
from decimal import Decimal as D
from unittest import mock

import pytest

from app.db import execute, query_all, query_one
from app.services import auth_service, result_service

pytestmark = pytest.mark.integration
PASSWORD = "Passw0rd1"
NOW = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)
PDF = b"%PDF-1.4\nbody\n%%EOF"


def run(w, sql, params=()):
    with w["app"].app_context():
        return execute(sql, params).lastrowid


def one(w, sql, params=()):
    with w["app"].app_context():
        return query_one(sql, params)


def many(w, sql, params=()):
    with w["app"].app_context():
        return query_all(sql, params)


@pytest.fixture
def w(client, integration_app, tmp_path, monkeypatch):
    """t1 teaches S1 (class A) and S2 (class A); t2 teaches S3 (class B). s1, s2 are in class A (enrolled in S1, S2);
    s3 is in class A but NOT enrolled; s4 is in class B. A default grading scheme exists."""
    monkeypatch.setitem(integration_app.config, "UPLOAD_FOLDER", str(tmp_path))
    with integration_app.app_context():
        dept = query_one("SELECT id FROM departments LIMIT 1")["id"]
        ay = execute("INSERT INTO academic_years (label, start_date, end_date, is_current) VALUES ('2026-27', '2026-06-01', '2027-05-31', 1)").lastrowid
        sem = execute("INSERT INTO semesters (academic_year_id, semester_number, name, start_date, end_date, is_current) VALUES (%s, 3, 'Semester 3', '2026-06-01', '2026-12-31', 1)", (ay,)).lastrowid
        cls_a = execute("INSERT INTO classes (department_id, academic_year_id, name, section) VALUES (%s, %s, 'SE', 'A')", (dept, ay)).lastrowid
        cls_b = execute("INSERT INTO classes (department_id, academic_year_id, name, section) VALUES (%s, %s, 'SE', 'B')", (dept, ay)).lastrowid
        subj = {c: execute("INSERT INTO subjects (code, name, department_id, semester_number, credits) VALUES (%s, %s, %s, 3, %s)", (c, f"Subject {c}", dept, cr)).lastrowid
                for c, cr in (("S1", 4), ("S2", 3), ("S3", 2))}
        scheme = execute("INSERT INTO grading_schemes (name, is_default) VALUES ('Test scheme', 1)").lastrowid
        for label, lo, hi, gp, ok in (("O", 90, 100, 10, 1), ("A", 70, 89.99, 8, 1), ("B", 50, 69.99, 6, 1), ("F", 0, 49.99, 0, 0)):
            execute("INSERT INTO grading_scheme_rules (scheme_id, grade_label, min_percent, max_percent, grade_point, is_pass) VALUES (%s, %s, %s, %s, %s, %s)", (scheme, label, lo, hi, gp, ok))
        u = {}
        for key, role, cls in (("t1", "teacher", None), ("t2", "teacher", None), ("s1", "student", cls_a), ("s2", "student", cls_a),
                               ("s3", "student", cls_a), ("s4", "student", cls_b), ("adm", "admin", None)):
            uid = execute("INSERT INTO users (email, password_hash, full_name, role, status) VALUES (%s, %s, %s, %s, 'active')",
                          (f"{key}@x.test", auth_service.hash_password(PASSWORD), f"User {key}", role)).lastrowid
            u[key] = uid
            if role == "student":
                execute("INSERT INTO student_profiles (user_id, roll_number, department_id, class_id, current_semester_id) VALUES (%s, %s, %s, %s, %s)", (uid, f"R-{key}", dept, cls, sem))
            elif role == "teacher":
                execute("INSERT INTO teacher_profiles (user_id, employee_code, department_id) VALUES (%s, %s, %s)", (uid, f"E-{key}", dept))
        ts = lambda t, s, c: execute("INSERT INTO teacher_subjects (teacher_id, subject_id, class_id, semester_id) VALUES (%s, %s, %s, %s)", (u[t], subj[s], c, sem)).lastrowid
        u.update(a1=ts("t1", "S1", cls_a), a2=ts("t1", "S2", cls_a), a3=ts("t2", "S3", cls_b))
        for s in ("s1", "s2"):
            for c in ("S1", "S2"):
                execute("INSERT INTO enrollments (student_id, subject_id, class_id, semester_id) VALUES (%s, %s, %s, %s)", (u[s], subj[c], cls_a, sem))
        execute("INSERT INTO enrollments (student_id, subject_id, class_id, semester_id) VALUES (%s, %s, %s, %s)", (u["s4"], subj["S3"], cls_b, sem))
        u.update(dept=dept, ay=ay, sem=sem, cls_a=cls_a, cls_b=cls_b, subj=subj, scheme=scheme, tmp=tmp_path, app=integration_app)
    return u


def sign_in(w, email):
    c = w["app"].test_client()
    c.get("/login")
    assert c.post("/login", data={"email": email, "password": PASSWORD}).status_code == 302
    return c


def finished_session(w, assignment="a1", present=("s1",), ended_minutes_ago=30):
    sid = run(w, "INSERT INTO attendance_sessions (assignment_id, status, started_at, session_ends_at, closed_at, qr_validity_seconds, geofence_enabled) "
                 "VALUES (%s, 'closed', %s, %s, %s, 120, 0)", (w[assignment], NOW - datetime.timedelta(minutes=ended_minutes_ago + 10),
                                                              NOW - datetime.timedelta(minutes=ended_minutes_ago), NOW - datetime.timedelta(minutes=ended_minutes_ago)))
    for s in present:
        run(w, "INSERT INTO attendance_records (session_id, student_id, marked_at) VALUES (%s, %s, %s)", (sid, w[s], NOW - datetime.timedelta(minutes=ended_minutes_ago + 5)))
    return sid


# ============================== access scope
def test_teacher_sees_only_assigned_classes_and_cannot_probe_others(w):
    t1, t2 = sign_in(w, "t1@x.test"), sign_in(w, "t2@x.test")
    page = t1.get("/teacher/classes").data
    assert b"S1" in page and b"S2" in page and b"S3" not in page
    for path in (f"/teacher/classes/{w['a3']}", f"/teacher/results/{w['a3']}", f"/teacher/results/{w['a3']}/export",
                 f"/teacher/syllabus/{w['subj']['S3']}"):
        assert t1.get(path).status_code == 404, path
    for path in (f"/teacher/classes/{w['a1']}", f"/teacher/results/{w['a1']}", f"/teacher/syllabus/{w['subj']['S1']}"):
        assert t2.get(path).status_code == 404, path
    assert t1.get("/teacher/classes/999999").status_code == 404
    assert sign_in(w, "s1@x.test").get("/teacher/").status_code == 403 and sign_in(w, "adm@x.test").get("/teacher/").status_code == 403
    assert w["app"].test_client().get("/teacher/").status_code == 302


def test_dashboard_statistics_come_from_the_database(w):
    t1 = sign_in(w, "t1@x.test")
    assert b"No sessions yet" in t1.get("/teacher/").data                                             # nothing held: no invented figures
    finished_session(w, "a1", present=("s1", "s2"))
    finished_session(w, "a1", present=("s1",))
    page = t1.get("/teacher/").data
    assert b"75.0%" in page                                                                           # (2+1) / (2 sessions x 2 students) = 75 %... see below
    s = one(w, "SELECT COUNT(*) AS c FROM attendance_sessions")["c"]
    assert s == 2


def test_student_wise_and_subject_wise_reports_with_csv(w):
    finished_session(w, "a1", present=("s1", "s2"))
    finished_session(w, "a1", present=("s1",))
    t1 = sign_in(w, "t1@x.test")
    report = t1.get(f"/teacher/classes/{w['a1']}").data
    assert b"2 / 2" in report and b"1 / 2" in report and b"50.0%" in report
    csv = t1.get(f"/teacher/reports?assignment_id={w['a1']}&format=csv")
    assert csv.mimetype == "text/csv" and b"R-s1,User s1,2,2,100.0" in csv.data.replace(b"\r", b"")
    assert b"S1,Subject S1,SE A" in t1.get("/teacher/reports?format=csv").data.replace(b"\r", b"")
    assert sign_in(w, "t2@x.test").get(f"/teacher/reports?assignment_id={w['a1']}").status_code == 404


def test_session_history_filters_and_ownership(w):
    mine = finished_session(w, "a1")
    other = finished_session(w, "a3", present=("s4",))
    t1 = sign_in(w, "t1@x.test")
    ids = lambda q: [i for i in (mine, other) if f"/sessions/{i}\"".encode() in t1.get(f"/teacher/sessions?{q}").data]
    assert ids("") == [mine] and ids(f"assignment_id={w['a1']}") == [mine] and ids("state=closed") == [mine] and ids("state=active") == []
    assert ids("from=2999-01-01") == [] and ids(f"semester_id={w['sem']}") == [mine]
    assert t1.get(f"/teacher/sessions/{other}").status_code == 404


def test_teacher_can_start_and_only_the_owner_can_close(w):
    t1, t2 = sign_in(w, "t1@x.test"), sign_in(w, "t2@x.test")
    r = t1.post("/api/teacher/attendance-session", json={"assignment_id": w["a1"], "geofence_enabled": False})
    assert r.status_code == 201
    sid = r.get_json()["data"]["id"]
    assert t2.post(f"/api/teacher/attendance-session/{sid}/close").status_code == 404
    assert t2.post("/api/teacher/attendance-session", json={"assignment_id": w["a1"], "geofence_enabled": False}).status_code == 403
    assert t1.post(f"/api/teacher/attendance-session/{sid}/close").get_json()["data"]["state"] == "closed"


# ============================== attendance corrections
def correct(client, sid, student, present, reason="Student was present; phone battery died"):
    return client.post(f"/teacher/sessions/{sid}/correct", data={"student_id": w_id(student), "present": "1" if present else "0", "reason": reason})


def w_id(student):
    return student


def test_correction_adds_attendance_keeps_history_and_updates_reports(w):
    sid = finished_session(w, "a1", present=("s1",))
    t1 = sign_in(w, "t1@x.test")
    assert correct(t1, sid, w["s2"], True).status_code == 302
    rec = one(w, "SELECT source FROM attendance_records WHERE session_id = %s AND student_id = %s", (sid, w["s2"]))
    corr = one(w, "SELECT * FROM attendance_corrections WHERE session_id = %s", (sid,))
    assert rec["source"] == "correction" and (corr["previous_present"], corr["new_present"]) == (0, 1)
    assert corr["corrected_by"] == w["t1"] and "battery" in corr["reason"] and corr["corrected_at"] is not None
    audit = one(w, "SELECT user_id, details FROM audit_logs WHERE action = 'attendance.correct'")
    details = json.loads(audit["details"]) if isinstance(audit["details"], str) else audit["details"]
    assert audit["user_id"] == w["t1"] and details["from"] == "absent" and details["to"] == "present" and details["student_id"] == w["s2"]
    s2 = sign_in(w, "s2@x.test").get("/api/student/attendance").get_json()["data"]["summary"]
    assert (s2["attended"], s2["conducted"]) == (1, 1)                                                # the student's totals follow the record
    assert b"(corrected)" in t1.get(f"/teacher/sessions/{sid}").data and b"battery" in t1.get(f"/teacher/sessions/{sid}").data


def test_removing_attendance_preserves_the_original_values(w):
    sid = finished_session(w, "a1", present=("s1",))
    original = one(w, "SELECT marked_at FROM attendance_records WHERE session_id = %s", (sid,))["marked_at"]
    t1 = sign_in(w, "t1@x.test")
    assert correct(t1, sid, w["s1"], False, "Scanned for a friend, confirmed absent").status_code == 302
    assert one(w, "SELECT COUNT(*) AS c FROM attendance_records WHERE session_id = %s", (sid,))["c"] == 0
    corr = one(w, "SELECT * FROM attendance_corrections")
    assert (corr["previous_present"], corr["new_present"], corr["old_marked_at"], corr["old_source"]) == (1, 0, original, "qr")
    # a session whose only attendee was corrected away still counts as held
    rep = sign_in(w, "t1@x.test").get(f"/teacher/reports?assignment_id={w['a1']}&format=csv").data.replace(b"\r", b"")
    assert b"R-s1,User s1,0,1,0.0" in rep


def test_correction_rules(w):
    sid = finished_session(w, "a1", present=("s1",))
    live = run(w, "INSERT INTO attendance_sessions (assignment_id, status, started_at, session_ends_at, qr_validity_seconds, geofence_enabled) VALUES (%s, 'active', %s, %s, 120, 0)",
               (w["a2"], NOW, NOW + datetime.timedelta(minutes=30)))
    t1, t2 = sign_in(w, "t1@x.test"), sign_in(w, "t2@x.test")
    follow = lambda client, s, st, p, reason="A valid reason": client.post(f"/teacher/sessions/{s}/correct", data={"student_id": st, "present": p, "reason": reason}, follow_redirects=True)
    assert b"Close the session before correcting" in follow(t1, live, w["s1"], "0").data               # still running
    assert b"Give a reason" in follow(t1, sid, w["s2"], "1", "").data and b"Give a reason" in follow(t1, sid, w["s2"], "1", "abc").data
    assert b"already the recorded attendance" in follow(t1, sid, w["s1"], "1").data
    assert follow(t1, sid, w["s3"], "1").status_code == 404                                           # not enrolled in the class
    assert t2.post(f"/teacher/sessions/{sid}/correct", data={"student_id": w["s2"], "present": "1", "reason": "Valid reason"}).status_code == 404
    assert sign_in(w, "s1@x.test").post(f"/teacher/sessions/{sid}/correct", data={"student_id": w["s1"], "present": "0", "reason": "Valid reason"}).status_code == 403
    assert one(w, "SELECT COUNT(*) AS c FROM attendance_corrections")["c"] == 0                       # nothing slipped through


# ============================== results
def post_results(client, assignment, **marks):
    data = {k: v for k, v in marks.items()}
    return client.post(f"/teacher/results/{assignment}", data=data)


def test_draft_results_derive_the_grade_on_the_server_and_stay_hidden(w):
    t1 = sign_in(w, "t1@x.test")
    r = post_results(t1, w["a1"], **{f"marks_{w['s1']}": "85", f"max_{w['s1']}": "100", f"marks_{w['s2']}": "92", f"max_{w['s2']}": "100",
                                     f"grade_{w['s1']}": "O", "grade_label": "O", "grade_point": "10", "is_published": "1"})
    assert r.status_code == 302
    rows = {x["student_id"]: x for x in many(w, "SELECT * FROM academic_results")}
    a, b = rows[w["s1"]], rows[w["s2"]]
    assert (a["grade_label"], a["grade_point"], a["credits_snapshot"], a["is_published"], a["scheme_id"]) == ("A", D("8.00"), D("4.0"), 0, w["scheme"])
    assert (b["grade_label"], b["grade_point"]) == ("O", D("10.00")) and a["entered_by"] == w["t1"]
    assert sign_in(w, "s1@x.test").get("/api/student/results").get_json()["data"]["has_results"] is False        # drafts are invisible
    assert b"Draft" in t1.get(f"/teacher/results/{w['a1']}").data


def test_invalid_marks_reject_the_whole_save(w):
    t1 = sign_in(w, "t1@x.test")
    r = post_results(t1, w["a1"], **{f"marks_{w['s1']}": "80", f"marks_{w['s2']}": "101"})
    assert r.status_code == 422 and b"Marks must be between 0 and 100" in r.data and b'value="101"' in r.data
    assert one(w, "SELECT COUNT(*) AS c FROM academic_results")["c"] == 0                                         # the valid row was not saved either
    for bad in ("-5", "abc", "1.234", "NaN"):
        assert post_results(t1, w["a1"], **{f"marks_{w['s1']}": bad}).status_code == 422, bad
    assert post_results(t1, w["a1"], **{f"marks_{w['s3']}": "60"}).status_code == 422                            # s3 is not in the class
    assert post_results(t1, w["a1"], **{f"marks_{w['s4']}": "60"}).status_code == 422                            # s4 is in another class
    assert post_results(t1, w["a1"], **{f"marks_{w['s1']}": "45", f"max_{w['s1']}": "50"}).status_code == 302   # 90 % of 50 -> "O"
    assert one(w, "SELECT grade_label FROM academic_results")["grade_label"] == "O"


def test_publishing_protects_results_and_feeds_the_student_calculations(w):
    t1 = sign_in(w, "t1@x.test")
    post_results(t1, w["a1"], **{f"marks_{w['s1']}": "85", f"marks_{w['s2']}": "55", "action": "save"})
    assert post_results(t1, w["a1"], **{f"marks_{w['s1']}": "85", "action": "publish"}).status_code == 302
    assert one(w, "SELECT COUNT(*) AS c FROM academic_results WHERE is_published = 1")["c"] == 2
    again = post_results(t1, w["a1"], **{f"marks_{w['s1']}": "99", "action": "save"})
    assert again.status_code == 422 and b"Already published" in again.data
    assert one(w, "SELECT marks_obtained FROM academic_results WHERE student_id = %s", (w["s1"],))["marks_obtained"] == D("85.00")
    s1 = sign_in(w, "s1@x.test").get("/api/student/academic-history").get_json()["data"]                          # Phase 4 reads the same rows
    assert s1["cgpa"] == "8.00" and D(s1["total_credits"]) == 4
    assert one(w, "SELECT COUNT(*) AS c FROM academic_results")["c"] == 2                                          # no duplicates
    actions = [r["action"] for r in many(w, "SELECT action FROM audit_logs WHERE action LIKE 'result.%' ORDER BY id")]
    assert actions.count("result.save") >= 2 and "result.publish" in actions
    assert sign_in(w, "s1@x.test").post(f"/teacher/results/{w['a1']}", data={f"marks_{w['s1']}": "100"}).status_code == 403


def test_unassigned_teacher_cannot_read_or_write_results(w):
    t2 = sign_in(w, "t2@x.test")
    assert t2.get(f"/teacher/results/{w['a1']}").status_code == 404
    assert post_results(t2, w["a1"], **{f"marks_{w['s1']}": "100", "action": "publish"}).status_code == 404
    assert one(w, "SELECT COUNT(*) AS c FROM academic_results")["c"] == 0


def test_institution_can_reserve_publishing_to_administrators(w):
    w["app"].config["TEACHER_CAN_PUBLISH_RESULTS"] = False
    try:
        t1 = sign_in(w, "t1@x.test")
        assert b"Save and publish" not in t1.get(f"/teacher/results/{w['a1']}").data
        assert post_results(t1, w["a1"], **{f"marks_{w['s1']}": "85", "action": "publish"}).status_code == 403
        assert post_results(t1, w["a1"], **{f"marks_{w['s1']}": "85", "action": "save"}).status_code == 302          # drafts still allowed
    finally:
        w["app"].config["TEACHER_CAN_PUBLISH_RESULTS"] = True


def test_result_save_is_atomic(w):
    t1 = sign_in(w, "t1@x.test")
    with mock.patch.object(result_service.audit_service, "log", side_effect=RuntimeError("boom")):
        assert post_results(t1, w["a1"], **{f"marks_{w['s1']}": "85", "action": "save"}).status_code == 500
    assert one(w, "SELECT COUNT(*) AS c FROM academic_results")["c"] == 0
    assert post_results(t1, w["a1"], **{f"marks_{w['s1']}": "85", "action": "save"}).status_code == 302


def test_results_without_a_grading_scheme_are_refused(w):
    run(w, "DELETE FROM grading_schemes")
    t1 = sign_in(w, "t1@x.test")
    assert b"No default grading scheme" in t1.get(f"/teacher/results/{w['a1']}").data
    assert post_results(t1, w["a1"], **{f"marks_{w['s1']}": "85"}).status_code == 409


def test_results_export_is_scoped_and_labelled(w):
    t1 = sign_in(w, "t1@x.test")
    post_results(t1, w["a1"], **{f"marks_{w['s1']}": "85"})
    csv = t1.get(f"/teacher/results/{w['a1']}/export")
    assert csv.mimetype == "text/csv" and b"R-s1,User s1,85.00,100.00,A,8.00,Draft" in csv.data.replace(b"\r", b"")


# ============================== syllabus and materials
def unit(client, subject, **over):
    data = {"unit_number": "1", "title": "Arrays", "topics": "Array basics", "lecture_hours": "8", "is_published": "1"}
    data.update(over)
    return client.post(f"/teacher/syllabus/{subject}/units", data=data)


def test_teacher_manages_units_for_assigned_subjects_only(w):
    t1, t2 = sign_in(w, "t1@x.test"), sign_in(w, "t2@x.test")
    s1 = w["subj"]["S1"]
    assert unit(t1, s1).status_code == 302
    assert unit(t1, s1, title="Duplicate number").status_code == 422                       # unit numbers are unique per subject
    assert unit(t1, s1, unit_number="2", title="Hidden unit", is_published="").status_code == 302
    assert unit(t1, s1, unit_number="0").status_code == 422 and unit(t1, s1, unit_number="3", title="").status_code == 422
    assert unit(t2, s1, unit_number="9").status_code == 404                                # t2 does not teach S1
    student = sign_in(w, "s1@x.test").get("/api/student/syllabus?semester=3").get_json()["data"]
    s1_units = [u["title"] for sub in student["subjects"] if sub["code"] == "S1" for u in sub["units"]]
    assert s1_units == ["Arrays"]                                                           # the hidden unit is not shown to students
    unit_id = one(w, "SELECT id FROM syllabus WHERE title = 'Hidden unit'")["id"]
    assert t1.post(f"/teacher/syllabus/units/{unit_id}/publish", data={"publish": "1"}).status_code == 302
    assert t2.post(f"/teacher/syllabus/units/{unit_id}/publish", data={"publish": "0"}).status_code == 404
    assert t1.post(f"/teacher/syllabus/{s1}/units", data={"unit_id": unit_id, "unit_number": "2", "title": "Edited", "topics": "t", "is_published": "1"}).status_code == 302
    assert one(w, "SELECT title FROM syllabus WHERE id = %s", (unit_id,))["title"] == "Edited"
    history = many(w, "SELECT action FROM audit_logs WHERE entity_type = 'syllabus' ORDER BY id")
    assert {"syllabus.create", "syllabus.update", "syllabus.publish"} <= {h["action"] for h in history}
    assert b"syllabus" in t1.get(f"/teacher/syllabus/{s1}").data.lower()


def upload(client, subject, content=PDF, name="notes.pdf", **form):
    data = {"title": "Unit 1 notes", "description": "Intro", "publish": "1", "file": (io.BytesIO(content), name)}
    data.update(form)
    return client.post(f"/teacher/syllabus/{subject}/materials", data=data, content_type="multipart/form-data", follow_redirects=True)


def test_material_upload_validation_publishing_and_student_download(w):
    t1, t2, s1 = sign_in(w, "t1@x.test"), sign_in(w, "t2@x.test"), sign_in(w, "s1@x.test")
    subject = w["subj"]["S1"]
    assert b"Material uploaded" in upload(t1, subject).data
    row = one(w, "SELECT * FROM learning_materials")
    assert row["is_published"] == 1 and row["uploaded_by"] == w["t1"] and len(row["stored_filename"]) == 36 and row["stored_filename"].endswith(".pdf")
    assert (w["tmp"] / "materials" / row["stored_filename"]).read_bytes() == PDF and row["original_filename"] == "notes.pdf"
    got = s1.get(f"/syllabus/materials/{row['id']}/download")
    assert got.status_code == 200 and got.mimetype == "application/pdf" and got.data == PDF
    for bad_name, bad_content, message in (("virus.exe", PDF, b"Only PDF files"), ("fake.pdf", b"MZ not a pdf at all", b"not a valid PDF"), ("empty.pdf", b"", b"Choose a PDF")):
        assert message in upload(t1, subject, bad_content, bad_name).data
    assert one(w, "SELECT COUNT(*) AS c FROM learning_materials")["c"] == 1 and len(list((w["tmp"] / "materials").glob("*"))) == 1   # rejected files left nothing behind
    assert b"3 to 150" in upload(t1, subject, title="x").data
    assert upload(t2, subject).status_code == 404                                           # not t2's subject
    assert t1.post(f"/teacher/syllabus/materials/{row['id']}/publish", data={"publish": "0"}).status_code == 302
    assert s1.get(f"/syllabus/materials/{row['id']}/download").status_code == 404           # hidden again
    assert t2.post(f"/teacher/syllabus/materials/{row['id']}/publish", data={"publish": "1"}).status_code == 404
    assert t1.post(f"/teacher/syllabus/materials/{row['id']}/update", data={"title": "Renamed notes", "description": "New"}).status_code == 302
    assert one(w, "SELECT title, description FROM learning_materials")["title"] == "Renamed notes"
    assert {"material.upload", "material.unpublish", "material.update"} <= {a["action"] for a in many(w, "SELECT action FROM audit_logs")}
    assert b"material" in t1.get(f"/teacher/syllabus/{subject}").data


def test_oversized_upload_is_refused(w):
    w["app"].config["MATERIAL_MAX_BYTES"] = 1000
    try:
        assert b"larger than" in upload(sign_in(w, "t1@x.test"), w["subj"]["S1"], PDF + b"x" * 2000).data
    finally:
        w["app"].config["MATERIAL_MAX_BYTES"] = 10 * 1024 * 1024
    assert one(w, "SELECT COUNT(*) AS c FROM learning_materials")["c"] == 0


# ============================== notices
def notice(client, **over):
    data = {"title": "Lab moved", "body": "The lab is in room 4.", "category": "timetable", "class_id": "", "action": "publish"}
    data.update(over)
    return client.post("/teacher/notices/new", data=data, content_type="multipart/form-data")


def titles(client):
    return [n["title"] for n in client.get("/api/student/notices").get_json()["data"]["items"]]


def test_teacher_notices_are_scoped_to_their_classes_and_categories(w):
    t1 = sign_in(w, "t1@x.test")
    a_student, b_student = sign_in(w, "s1@x.test"), sign_in(w, "s4@x.test")
    assert notice(t1, class_id=w["cls_a"]).status_code == 302
    assert titles(a_student) == ["Lab moved"] and titles(b_student) == []                        # only the addressed class
    for bad in ({"class_id": w["cls_b"]}, {"class_id": ""}, {"class_id": w["cls_a"], "category": "fee"},
                {"class_id": w["cls_a"], "category": "holiday"}, {"class_id": w["cls_a"], "semester_number": "5"},
                {"class_id": w["cls_a"], "expires_at": "2000-01-01"}, {"class_id": w["cls_a"], "title": "x"}):
        assert notice(t1, title=bad.pop("title", "Another notice"), **bad).status_code == 422, bad
    assert titles(a_student) == ["Lab moved"]
    row = one(w, "SELECT audience, department_id, class_id, created_by, is_active FROM notices")
    assert (row["audience"], row["department_id"], row["class_id"], row["created_by"], row["is_active"]) == ("student", w["dept"], w["cls_a"], w["t1"], 1)


def test_drafts_and_withdrawn_notices_are_hidden_and_other_teachers_cannot_touch_them(w):
    t1, t2, s1 = sign_in(w, "t1@x.test"), sign_in(w, "t2@x.test"), sign_in(w, "s1@x.test")
    notice(t1, class_id=w["cls_a"], title="Draft notice", action="draft")
    assert titles(s1) == []
    nid = one(w, "SELECT id FROM notices")["id"]
    assert s1.get(f"/notices/{nid}").status_code == 404
    assert t1.post(f"/teacher/notices/{nid}/publish", data={"publish": "1"}).status_code == 302 and titles(s1) == ["Draft notice"]
    assert t1.post(f"/teacher/notices/{nid}/publish", data={"publish": "0"}).status_code == 302 and titles(s1) == []
    assert t2.get(f"/teacher/notices/{nid}/edit").status_code == 404
    assert t2.post(f"/teacher/notices/{nid}/publish", data={"publish": "1"}).status_code == 404
    admin_notice = run(w, "INSERT INTO notices (title, body, category, audience, is_active, created_by) VALUES ('From admin', 'x', 'holiday', 'all', 1, %s)", (w["adm"],))
    assert t1.get(f"/teacher/notices/{admin_notice}/edit").status_code == 404                  # administrative notices stay with administrators
    assert t1.post(f"/teacher/notices/{admin_notice}/publish", data={"publish": "0"}).status_code == 404
    assert one(w, "SELECT is_active FROM notices WHERE id = %s", (admin_notice,))["is_active"] == 1
    actions = {a["action"] for a in many(w, "SELECT action FROM audit_logs WHERE entity_type = 'notice'")}
    assert {"notice.create", "notice.publish", "notice.unpublish"} <= actions


def test_notice_attachment_is_validated_and_downloadable_by_the_class(w):
    t1, s1, s4 = sign_in(w, "t1@x.test"), sign_in(w, "s1@x.test"), sign_in(w, "s4@x.test")
    r = notice(t1, class_id=w["cls_a"], attachment=(io.BytesIO(PDF), "rules.pdf"))
    assert r.status_code == 302
    nid = one(w, "SELECT id, attachment_stored_name FROM notices")
    assert len(nid["attachment_stored_name"]) == 36
    got = s1.get(f"/notices/{nid['id']}/attachment")
    assert got.status_code == 200 and got.data == PDF
    assert s4.get(f"/notices/{nid['id']}/attachment").status_code == 404                        # other class: not visible
    bad = notice(t1, class_id=w["cls_a"], title="Bad file", attachment=(io.BytesIO(b"MZ binary"), "x.pdf"))
    assert bad.status_code == 422 and one(w, "SELECT COUNT(*) AS c FROM notices")["c"] == 1
    assert len(list((w["tmp"] / "notices").glob("*"))) == 1


# ============================== audit and migrations
def test_audit_entries_name_the_actor_and_never_contain_secrets(w):
    t1 = sign_in(w, "t1@x.test")
    notice(t1, class_id=w["cls_a"])
    post_results(t1, w["a1"], **{f"marks_{w['s1']}": "85", "action": "save"})
    sid = finished_session(w, "a1")
    correct(t1, sid, w["s2"], True)
    rows = many(w, "SELECT user_id, action, details, ip_address FROM audit_logs WHERE user_id = %s", (w["t1"],))
    assert {"notice.publish", "result.save", "attendance.correct", "login.success"} <= {r["action"] for r in rows}
    blob = json.dumps([[str(c) for c in r.values()] for r in many(w, "SELECT * FROM audit_logs")]).lower()
    for secret in ("passw0rd1", "pbkdf2", "password", "csrf", "session", "token"):
        assert secret not in blob.replace("login.success", "").replace("session_start", ""), secret


def test_append_only_triggers_when_installed(w):
    with w["app"].app_context():
        installed = {r["TRIGGER_NAME"] for r in query_all("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE()")}
    if "trg_audit_logs_no_update" not in installed:
        pytest.skip("Optional triggers not installed (python scripts\\protect_audit_tables.py --test)")
    sign_in(w, "t1@x.test")
    with w["app"].app_context():
        with pytest.raises(Exception, match="append-only"):
            execute("UPDATE audit_logs SET action = 'tampered'")
        with pytest.raises(Exception, match="append-only"):
            execute("DELETE FROM audit_logs")


def test_migration_003_is_applied(w):
    with w["app"].app_context():
        cols = {r["COLUMN_NAME"] for r in query_all("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                                                    "AND TABLE_NAME IN ('attendance_records', 'notices', 'syllabus', 'learning_materials')")}
        assert {"source", "class_id", "is_published", "description"} <= cols
        assert query_one("SELECT COUNT(*) AS c FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'attendance_corrections'")["c"] == 1
        assert "003_phase5_teacher_tools" in {r["version"] for r in query_all("SELECT version FROM schema_migrations")}
