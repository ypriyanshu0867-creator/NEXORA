"""PHASE 3 INTEGRATION TESTS - require a real MySQL test database.

Prepare once:   python scripts\init_db.py --bootstrap
                python scripts\init_db.py --test        (also applies migration 001)
Run:            python -m pytest tests -v
Skipped automatically when MySQL is unreachable. The test database is emptied
before every test. A controllable clock replaces real waiting.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

from app import clock
from app.db import IntegrityError, execute, query_all, query_one
from app.services import attendance_service, auth_service

pytestmark = pytest.mark.integration
PASSWORD = "Passw0rd1"
LAT, LNG = 20.0, 78.0
NEAR = {"latitude": 20.0001, "longitude": 78.0, "accuracy": 10}


@pytest.fixture
def fclock():
    fake = clock.FrozenClock()
    clock.set_clock(fake)
    yield fake
    clock.reset_clock()


def _user(role, email, name):
    uid = execute("INSERT INTO users (email, password_hash, full_name, role, status) "
                  "VALUES (%s, %s, %s, %s, 'active')",
                  (email, auth_service.hash_password(PASSWORD), name, role)).lastrowid
    dept = query_one("SELECT id FROM departments LIMIT 1")["id"]
    if role == "student":
        execute("INSERT INTO student_profiles (user_id, roll_number, department_id) VALUES (%s, %s, %s)",
                (uid, f"R-{uid}", dept))
    elif role == "teacher":
        execute("INSERT INTO teacher_profiles (user_id, employee_code, department_id) VALUES (%s, %s, %s)",
                (uid, f"E-{uid}", dept))
    return uid


@pytest.fixture
def world(client, integration_app, fclock):
    """Two teachers, three students (s3 is NOT enrolled), two subjects, one classroom."""
    with integration_app.app_context():
        dept = query_one("SELECT id FROM departments LIMIT 1")["id"]
        ay = execute("INSERT INTO academic_years (label, start_date, end_date, is_current) "
                     "VALUES ('2026-27', '2026-06-01', '2027-05-31', 1)").lastrowid
        sem = execute("INSERT INTO semesters (academic_year_id, semester_number, name, start_date, end_date, "
                      "is_current) VALUES (%s, 3, 'Semester 3', '2026-06-01', '2026-12-31', 1)", (ay,)).lastrowid
        cls = execute("INSERT INTO classes (department_id, academic_year_id, name, section) "
                      "VALUES (%s, %s, 'SE', 'A')", (dept, ay)).lastrowid
        sub1 = execute("INSERT INTO subjects (code, name, department_id, semester_number, credits) "
                       "VALUES ('T1', 'Subject One', %s, 3, 4)", (dept,)).lastrowid
        sub2 = execute("INSERT INTO subjects (code, name, department_id, semester_number, credits) "
                       "VALUES ('T2', 'Subject Two', %s, 3, 3)", (dept,)).lastrowid
        w = {"t1": _user("teacher", "t1@x.test", "Teacher One"), "t2": _user("teacher", "t2@x.test", "Teacher Two"),
             "s1": _user("student", "s1@x.test", "Student One"), "s2": _user("student", "s2@x.test", "Student Two"),
             "s3": _user("student", "s3@x.test", "Student Three"), "sem": sem, "cls": cls}
        w["a1"] = execute("INSERT INTO teacher_subjects (teacher_id, subject_id, class_id, semester_id) "
                          "VALUES (%s, %s, %s, %s)", (w["t1"], sub1, cls, sem)).lastrowid
        w["a2"] = execute("INSERT INTO teacher_subjects (teacher_id, subject_id, class_id, semester_id) "
                          "VALUES (%s, %s, %s, %s)", (w["t1"], sub2, cls, sem)).lastrowid
        for s in ("s1", "s2"):
            for sub in (sub1, sub2):
                execute("INSERT INTO enrollments (student_id, subject_id, class_id, semester_id) "
                        "VALUES (%s, %s, %s, %s)", (w[s], sub, cls, sem))
        w["room"] = execute("INSERT INTO classrooms (name, latitude, longitude, default_radius_m) "
                            "VALUES ('R1', %s, %s, 50)", (LAT, LNG)).lastrowid
    w["app"] = integration_app
    return w


def sign_in(app, email):
    c = app.test_client()
    c.get("/login")
    assert c.post("/login", data={"email": email, "password": PASSWORD}).status_code == 302
    return c


def start(tc, assignment, **over):
    body = {"assignment_id": assignment, "classroom_id": None, "qr_validity_seconds": 120,
            "duration_minutes": 10, "geofence_enabled": True, "latitude": LAT, "longitude": LNG,
            "radius_m": 50, "min_accuracy_m": 30}
    body.update(over)
    return tc.post("/api/teacher/attendance-session", json=body)


def new_qr(tc, session_id):
    return tc.post(f"/api/teacher/attendance-session/{session_id}/qr").get_json()["data"]["payload"]


def mark(c, qr, **loc):
    return c.post("/api/attendance/mark", json={"qr": qr, **(NEAR if not loc else loc)})


def live_session(w, assignment=None, **over):
    tc = sign_in(w["app"], "t1@x.test")
    sid = start(tc, assignment or w["a1"], **over).get_json()["data"]["id"]
    return tc, sid, new_qr(tc, sid)


def count(app, sql, params=()):
    with app.app_context():
        return query_one(sql, params)["c"]


# ============================ A. authentication and permissions
def test_unauthenticated_mark_is_rejected(world):
    r = world["app"].test_client().post("/api/attendance/mark", json={"qr": "x"})
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "authentication_required"


def test_student_cannot_use_teacher_endpoints(world):
    s1 = sign_in(world["app"], "s1@x.test")
    assert s1.get("/api/teacher/subjects").status_code == 403
    assert start(s1, world["a1"]).status_code == 403
    assert s1.get("/teacher/attendance").status_code == 403


def test_teacher_cannot_start_session_for_unassigned_subject(world):
    t2 = sign_in(world["app"], "t2@x.test")
    r = start(t2, world["a1"])                               # a1 belongs to teacher one
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "not_assigned"
    assert count(world["app"], "SELECT COUNT(*) AS c FROM attendance_sessions") == 0


def test_student_cannot_mark_attendance_for_another_student(world):
    _, sid, qr = live_session(world)
    s1 = sign_in(world["app"], "s1@x.test")
    r = s1.post("/api/attendance/mark", json={"qr": qr, "student_id": world["s2"], **NEAR})
    assert r.status_code == 201
    with world["app"].app_context():
        assert [x["student_id"] for x in query_all("SELECT student_id FROM attendance_records")] == [world["s1"]]


# ============================ B. QR validation
def test_valid_qr_marks_attendance_and_stores_no_coordinates(world):
    _, sid, qr = live_session(world)
    r = mark(sign_in(world["app"], "s1@x.test"), qr)
    assert r.status_code == 201 and r.get_json()["data"]["session_id"] == sid
    with world["app"].app_context():
        row = query_one("SELECT * FROM attendance_records")
        assert 10 < float(row["distance_m"]) < 12 and float(row["gps_accuracy_m"]) == 10
        assert not any("lat" in k or "lng" in k or "lon" in k for k in row)       # no coordinate columns


def test_expired_qr_is_rejected_by_the_server(world, fclock):
    _, sid, qr = live_session(world, qr_validity_seconds=120)
    s1 = sign_in(world["app"], "s1@x.test")
    fclock.advance(121)
    r = mark(s1, qr)
    assert r.status_code == 410 and r.get_json()["error"]["code"] == "qr_expired"
    assert count(world["app"], "SELECT COUNT(*) AS c FROM attendance_records") == 0


def test_invalid_and_malformed_qr(world):
    live_session(world)
    s1 = sign_in(world["app"], "s1@x.test")
    assert mark(s1, "NXR1." + "A" * 43).get_json()["error"]["code"] == "qr_invalid"      # well-formed, unknown
    assert mark(s1, "garbage").status_code == 400
    assert mark(s1, "").status_code == 400


def test_closed_session_rejects_even_an_unexpired_qr(world):
    tc, sid, qr = live_session(world)
    assert tc.post(f"/api/teacher/attendance-session/{sid}/close").status_code == 200
    r = mark(sign_in(world["app"], "s1@x.test"), qr)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "session_closed"


def test_token_is_bound_to_its_own_session(world):
    tc, sid1, _ = live_session(world, world["a1"])
    sid2 = start(tc, world["a2"]).get_json()["data"]["id"]
    qr2 = new_qr(tc, sid2)
    assert mark(sign_in(world["app"], "s1@x.test"), qr2).status_code == 201
    with world["app"].app_context():
        assert query_one("SELECT session_id FROM attendance_records")["session_id"] == sid2   # not session 1


def test_qr_policy_replay_by_same_student_is_duplicate_but_other_students_may_use_the_shared_code(world):
    _, sid, qr = live_session(world)
    s1, s2 = sign_in(world["app"], "s1@x.test"), sign_in(world["app"], "s2@x.test")
    assert mark(s1, qr).status_code == 201
    assert mark(s1, qr).status_code == 409
    assert mark(s2, qr).status_code == 201                  # multi-use within validity is intentional


def test_only_hash_of_token_is_stored(world):
    _, sid, qr = live_session(world)
    token = qr.split(".", 1)[1]
    with world["app"].app_context():
        rows = query_all("SELECT token_hash FROM attendance_qr_tokens")
        assert rows and all(token not in r["token_hash"] and len(r["token_hash"]) == 64 for r in rows)


# ============================ C. attendance rules
def test_duplicate_attendance_creates_one_record(world):
    _, sid, qr = live_session(world)
    s1 = sign_in(world["app"], "s1@x.test")
    assert mark(s1, qr).status_code == 201
    r = mark(s1, qr)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "already_marked"
    assert count(world["app"], "SELECT COUNT(*) AS c FROM attendance_records") == 1


def test_student_not_enrolled_is_rejected_and_logged(world):
    _, sid, qr = live_session(world)
    r = mark(sign_in(world["app"], "s3@x.test"), qr)
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "not_enrolled"
    assert count(world["app"], "SELECT COUNT(*) AS c FROM attendance_security_events "
                               "WHERE event_type = 'not_enrolled'") == 1


def test_concurrent_duplicate_requests_create_exactly_one_record(world):
    _, sid, qr = live_session(world)
    clients = [sign_in(world["app"], "s1@x.test") for _ in range(2)]         # same student, two devices
    barrier = threading.Barrier(2)

    def fire(c):
        barrier.wait()
        return mark(c, qr).status_code

    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(fire, clients))
    assert codes == [201, 409]
    assert count(world["app"], "SELECT COUNT(*) AS c FROM attendance_records") == 1


def test_database_rejects_duplicate_record_and_second_active_session(world):
    _, sid, _ = live_session(world)
    with world["app"].app_context():
        execute("INSERT INTO attendance_records (session_id, student_id, marked_at) VALUES (%s, %s, %s)",
                (sid, world["s1"], clock.now()))
        with pytest.raises(IntegrityError):
            execute("INSERT INTO attendance_records (session_id, student_id, marked_at) VALUES (%s, %s, %s)",
                    (sid, world["s1"], clock.now()))
        with pytest.raises(IntegrityError):               # migration 001's unique key
            execute("INSERT INTO attendance_sessions (assignment_id, status, started_at, session_ends_at, "
                    "qr_validity_seconds, geofence_enabled) VALUES (%s, 'active', %s, %s, 120, 0)",
                    (world["a1"], clock.now(), clock.now()))


def test_failure_after_verification_rolls_back_the_record(world):
    _, sid, qr = live_session(world)
    s1 = sign_in(world["app"], "s1@x.test")
    with mock.patch.object(attendance_service.audit_service, "log", side_effect=RuntimeError("boom")):
        assert mark(s1, qr).status_code == 500
    assert count(world["app"], "SELECT COUNT(*) AS c FROM attendance_records") == 0
    assert mark(s1, qr).status_code == 201                  # and the student can still mark afterwards


# ============================ D. location
def test_location_policy(world):
    _, sid, qr = live_session(world, radius_m=50, min_accuracy_m=30)
    s1 = sign_in(world["app"], "s1@x.test")
    expect = [
        ({"latitude": 20.01, "longitude": 78.0, "accuracy": 10}, 403, "outside_radius"),
        ({"latitude": 91, "longitude": 78.0, "accuracy": 10}, 422, "location_invalid"),
        ({"latitude": 20.0, "longitude": 181, "accuracy": 10}, 422, "location_invalid"),
        ({"latitude": 20.0, "longitude": 78.0, "accuracy": 80}, 422, "location_inaccurate"),
        ({"latitude": 20.0, "longitude": 78.0, "accuracy": -5}, 422, "location_invalid"),
        ({"location_error": "denied"}, 422, "location_denied"),
    ]
    for payload, status, code in expect:
        r = s1.post("/api/attendance/mark", json={"qr": qr, **payload})
        assert (r.status_code, r.get_json()["error"]["code"]) == (status, code), payload
    assert count(world["app"], "SELECT COUNT(*) AS c FROM attendance_records") == 0
    assert mark(s1, qr).status_code == 201                  # inside the radius
    with world["app"].app_context():
        types = {r["event_type"] for r in query_all("SELECT event_type FROM attendance_security_events")}
    assert {"outside_radius", "location_invalid", "location_inaccurate", "location_denied"} <= types


def test_missing_coordinates_rejected_when_geofence_on_but_not_when_off(world):
    _, sid, qr = live_session(world, world["a1"], geofence_enabled=True)
    s1 = sign_in(world["app"], "s1@x.test")
    assert s1.post("/api/attendance/mark", json={"qr": qr}).get_json()["error"]["code"] == "location_missing"
    tc2, sid2, qr2 = live_session(world, world["a2"], geofence_enabled=False)
    r = s1.post("/api/attendance/mark", json={"qr": qr2})
    assert r.status_code == 201 and r.get_json()["data"]["distance_m"] is None


def test_session_keeps_its_geofence_snapshot_when_the_classroom_changes(world):
    tc = sign_in(world["app"], "t1@x.test")
    r = start(tc, world["a1"], classroom_id=world["room"], latitude=None, longitude=None)
    assert r.status_code == 201 and r.get_json()["data"]["geofence"]["latitude"] == LAT
    with world["app"].app_context():
        execute("UPDATE classrooms SET latitude = 30, longitude = 90 WHERE id = %s", (world["room"],))
    sid = r.get_json()["data"]["id"]
    assert tc.get(f"/api/teacher/attendance-session/{sid}").get_json()["data"]["geofence"]["latitude"] == LAT


# ============================ E. device cooldown
def login_response(c, email):
    return c.post("/login", data={"email": email, "password": PASSWORD})


def test_device_registration_login_and_events(world):
    sign_in(world["app"], "s1@x.test")
    with world["app"].app_context():
        dev = query_one("SELECT * FROM device_registrations")
        assert dev["active_user_id"] == world["s1"] and len(dev["device_hash"]) == 64
        kinds = [r["event_type"] for r in query_all("SELECT event_type FROM device_events ORDER BY id")]
    assert kinds == ["register", "login"]


def test_logout_without_active_attendance_allows_immediate_account_switch(world):
    a = sign_in(world["app"], "s1@x.test")
    assert a.post("/logout").status_code == 302

    # No relevant attendance session: another student can log in immediately.
    assert login_response(a, "s2@x.test").status_code == 302


def test_logout_cooldown_blocks_other_student_but_not_same_student(world, fclock):
    _, _, _ = live_session(world)
    a = sign_in(world["app"], "s1@x.test")

    assert a.post("/logout").status_code == 302

    r = login_response(a, "s2@x.test")
    assert r.status_code == 429 and r.headers["Retry-After"] == "180"
    assert b"180 second" in r.data
    assert b"Student One" not in r.data and b"s1@x.test" not in r.data

    fclock.advance(60)
    assert int(login_response(a, "s2@x.test").headers["Retry-After"]) == 120

    # The previous student can log back in immediately.
    assert login_response(a, "s1@x.test").status_code == 302


def test_other_student_allowed_after_cooldown(world, fclock):
    _, _, _ = live_session(world)
    a = sign_in(world["app"], "s1@x.test")

    a.post("/logout")
    fclock.advance(181)

    assert login_response(a, "s2@x.test").status_code == 302


def test_closing_attendance_session_removes_cooldown_immediately(world):
    tc, sid, _ = live_session(world)
    a = sign_in(world["app"], "s1@x.test")

    a.post("/logout")

    # Confirm cooldown is active before closing the session.
    blocked = login_response(a, "s2@x.test")
    assert blocked.status_code == 429

    # Closing the relevant session must remove the restriction immediately.
    assert tc.post(f"/api/teacher/attendance-session/{sid}/close").status_code == 200
    assert login_response(a, "s2@x.test").status_code == 302


def test_expired_attendance_session_removes_cooldown_before_180_seconds(world, fclock):
    _, _, _ = live_session(world, duration_minutes=1)
    a = sign_in(world["app"], "s1@x.test")

    a.post("/logout")

    # The session expires after 60 seconds, before the 180-second cooldown.
    fclock.advance(61)

    assert login_response(a, "s2@x.test").status_code == 302


def test_unenrolled_student_logout_does_not_start_cooldown(world):
    _, _, _ = live_session(world)
    a = sign_in(world["app"], "s3@x.test")

    assert a.post("/logout").status_code == 302

    with world["app"].app_context():
        dev = query_one("SELECT cooldown_until FROM device_registrations")
        assert dev["cooldown_until"] is None


def test_missing_or_invalid_device_cookie_blocks_student_login_but_not_teacher(world):
    c = world["app"].test_client()  # Never loaded the login page.
    assert login_response(c, "s1@x.test").status_code == 400

    c.set_cookie("nexora_device", "garbage")
    assert login_response(c, "s1@x.test").status_code == 400

    assert login_response(c, "t1@x.test").status_code == 302


def test_account_switch_without_logout_starts_cooldown(world, fclock):
    _, _, _ = live_session(world)
    a = sign_in(world["app"], "s1@x.test")

    b = world["app"].test_client()
    b.set_cookie("nexora_device", a.get_cookie("nexora_device").value)

    assert login_response(b, "s2@x.test").status_code == 429

    fclock.advance(181)
    assert login_response(b, "s2@x.test").status_code == 302


def test_concurrent_account_switch_attempts_are_serialised(world, fclock):
    _, _, _ = live_session(world)
    a = sign_in(world["app"], "s1@x.test")
    value = a.get_cookie("nexora_device").value

    others = []
    for email in ("s2@x.test", "s3@x.test"):
        c = world["app"].test_client()
        c.set_cookie("nexora_device", value)
        others.append((c, email))

    barrier = threading.Barrier(2)

    def attempt(item):
        barrier.wait()
        return login_response(*item).status_code

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(attempt, others)) == [429, 429]

    assert count(
        world["app"],
        "SELECT COUNT(*) AS c FROM device_events WHERE event_type = 'account_switch'"
    ) == 1

    fclock.advance(181)

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, others)) == [302, 429]


def test_one_device_cannot_mark_two_students_in_one_session(world, fclock):
    tc, sid, qr = live_session(world)
    phone = sign_in(world["app"], "s1@x.test")

    assert mark(phone, qr).status_code == 201

    phone.post("/logout")
    fclock.advance(181)

    assert login_response(phone, "s2@x.test").status_code == 302

    # Fresh QR ensures this test checks anti-proxy, not QR expiry.
    fresh_qr = new_qr(tc, sid)

    r = mark(phone, fresh_qr)
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "device_reused"

def test_marking_requires_the_device_cookie(world):
    _, sid, qr = live_session(world)
    s1 = sign_in(world["app"], "s1@x.test")

    s1.delete_cookie("nexora_device")
    r = mark(s1, qr)

    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "device_unregistered"


def test_device_status_endpoint(world, fclock):
    c = sign_in(world["app"], "s1@x.test")

    assert c.get("/api/device/status").get_json()["data"]["bound_to_you"] is True

    c.post("/logout")

    assert sign_in(world["app"], "t1@x.test").get(
        "/api/device/status"
    ).get_json()["data"] == {"applicable": False}




# ============================ F. session management and reports
def test_start_close_and_authorisation(world):
    t1, t2 = sign_in(world["app"], "t1@x.test"), sign_in(world["app"], "t2@x.test")
    sid = start(t1, world["a1"]).get_json()["data"]["id"]
    assert start(t1, world["a1"]).get_json()["error"]["code"] == "session_already_active"
    assert t2.post(f"/api/teacher/attendance-session/{sid}/close").status_code == 404      # not their session
    assert t2.get(f"/api/teacher/attendance-session/{sid}").status_code == 404
    closed = t1.post(f"/api/teacher/attendance-session/{sid}/close").get_json()["data"]
    assert closed["state"] == "closed"
    assert start(t1, world["a1"]).status_code == 201                                       # may start a new one


def test_qr_expiry_and_session_duration_are_enforced_independently(world, fclock):
    tc, sid, qr = live_session(world, qr_validity_seconds=30, duration_minutes=10)
    s1 = sign_in(world["app"], "s1@x.test")
    fclock.advance(31)

    assert mark(s1, qr).get_json()["error"]["code"] == "qr_expired"
    assert tc.get(f"/api/teacher/attendance-session/{sid}").get_json()["data"]["state"] == "active"

    r = mark(s1, new_qr(tc, sid))
    print("ROTATED QR RESPONSE:", r.status_code, r.get_json())
    assert r.status_code == 201  # session still open: rotated QR works

    fclock.advance(10 * 60)
    assert tc.post(f"/api/teacher/attendance-session/{sid}/qr").get_json()["error"]["code"] == "session_closed"
    assert mark(sign_in(world["app"], "s2@x.test"), qr).get_json()["error"]["code"] == "session_closed"


def test_attendance_reports_percentages_csv_and_privacy(world, fclock):
    tc, sid, qr = live_session(world)
    mark(sign_in(world["app"], "s1@x.test"), qr)
    tc.post(f"/api/teacher/attendance-session/{sid}/close")
    sid2 = start(tc, world["a1"]).get_json()["data"]["id"]             # a second session nobody attends,
    tc.post(f"/api/teacher/attendance-session/{sid2}/close")           # closed with zero records = not held
    detail = tc.get(f"/api/teacher/attendance-session/{sid}").get_json()["data"]
    assert detail["present_count"] == 1 and detail["enrolled_count"] == 2
    csv = tc.get(f"/api/teacher/attendance?session_id={sid}&format=csv")
    assert csv.mimetype == "text/csv" and b"Student One" in csv.data and csv.data.startswith(b"Session ID")

    s1, s2, s3 = (sign_in(world["app"], f"{n}@x.test") for n in ("s1", "s2", "s3"))
    one = s1.get("/api/student/attendance").get_json()["data"]["summary"]
    two = s2.get("/api/student/attendance").get_json()["data"]["summary"]
    assert (one["attended"], one["conducted"], one["overall_percent"]) == (1, 1, 100.0)    # empty session not counted
    assert (two["attended"], two["conducted"], two["overall_percent"]) == (0, 1, 0.0)
    assert s3.get("/api/student/attendance").get_json()["data"]["summary"]["overall_percent"] is None
    assert s3.get(f"/api/attendance/session/{sid}").status_code == 404           # not enrolled: no access
    assert sign_in(world["app"], "t2@x.test").get(f"/api/teacher/attendance?session_id={sid}").get_json()["data"]["records"] == []
