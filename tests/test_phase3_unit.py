"""PHASE 3 UNIT TESTS - no MySQL needed (pure logic, templates, cookies, JSON envelope).

They do NOT prove that any SQL works. The MySQL-backed checks are in
tests/test_phase3_integration.py.
"""
import math
from datetime import datetime, timedelta

import pytest

from app import clock
from app.services import device_service, geo_service, qr_service, report_service, session_service
from app.services.errors import ServiceError

SESSION = {"geofence_enabled": 1, "geo_latitude": 20.0, "geo_longitude": 78.0,
           "geo_radius_m": 50, "geo_min_accuracy_m": 30}
T0 = datetime(2030, 1, 1, 12, 0, 0)


def code_of(fn, *args):
    with pytest.raises(ServiceError) as info:
        fn(*args)
    return info.value.code


# ------------------------------------------------------------------ geo
def test_haversine_known_distances():
    assert geo_service.haversine_m(20, 78, 20, 78) == 0
    assert math.isclose(geo_service.haversine_m(0, 0, 0, 1), 111_195, rel_tol=0.001)
    assert math.isclose(geo_service.haversine_m(0, 0, 1, 0), 111_195, rel_tol=0.001)
    assert math.isclose(geo_service.haversine_m(0, 179.5, 0, -179.5), 111_195, rel_tol=0.001)  # dateline


def test_location_inside_radius_is_accepted_and_returns_only_distance():
    result = geo_service.verify_for_session(SESSION, {"latitude": 20.0001, "longitude": 78.0, "accuracy": 10})
    assert 10 < result["distance_m"] < 12 and result["accuracy_m"] == 10
    assert set(result) == {"distance_m", "accuracy_m"}            # no raw coordinates leave this function


def test_location_failures():
    verify = geo_service.verify_for_session
    assert code_of(verify, SESSION, {"latitude": 20.01, "longitude": 78.0, "accuracy": 10}) == "outside_radius"
    assert code_of(verify, SESSION, {"latitude": 20.0, "longitude": 78.0, "accuracy": 80}) == "location_inaccurate"
    assert code_of(verify, SESSION, {}) == "location_missing"
    assert code_of(verify, SESSION, {"location_error": "denied"}) == "location_denied"
    for bad in ({"latitude": 91, "longitude": 78, "accuracy": 5}, {"latitude": 20, "longitude": 181, "accuracy": 5},
                {"latitude": "20", "longitude": 78, "accuracy": 5}, {"latitude": True, "longitude": 78, "accuracy": 5},
                {"latitude": float("nan"), "longitude": 78, "accuracy": 5}, {"latitude": 20, "longitude": 78, "accuracy": -1},
                {"latitude": 20, "longitude": 78, "accuracy": 0}, {"latitude": 20, "longitude": 78, "accuracy": 1e9},
                {"latitude": 20, "longitude": 78}):
        assert code_of(verify, SESSION, bad) == "location_invalid", bad


def test_geofence_disabled_needs_no_location():
    assert geo_service.verify_for_session(dict(SESSION, geofence_enabled=0), {}) is None


def test_boundary_is_inclusive():
    session = dict(SESSION, geo_radius_m=int(geo_service.haversine_m(20, 78, 20.0005, 78)) + 1)
    assert geo_service.verify_for_session(session, {"latitude": 20.0005, "longitude": 78.0, "accuracy": 5})


# ------------------------------------------------------------------- qr
def test_qr_token_format_hash_and_payload():
    token = qr_service.new_token()
    assert qr_service.TOKEN_RE.match(token) and token != qr_service.new_token()
    assert qr_service.hash_token(token) == qr_service.hash_token(token) and len(qr_service.hash_token(token)) == 64
    assert qr_service.hash_token(token) != token
    assert qr_service.parse_payload("  " + qr_service.build_payload(token) + "\n") == token


def test_qr_payload_rejects_malformed_input():
    good = qr_service.new_token()
    for bad in ("", None, 5, good, "NXR2." + good, "NXR1." + good[:-1], "NXR1." + good + "x",
                "NXR1." + "!" * 43, "NXR1." + good + "A" * 500, "http://evil.example/" + good):
        assert code_of(qr_service.parse_payload, bad) == "qr_invalid", bad


# ---------------------------------------------------------------- clock
def test_frozen_clock_advances_and_truncates():
    fake = clock.FrozenClock(T0)
    clock.set_clock(fake)
    try:
        assert clock.now() == T0
        fake.advance(181)
        assert clock.now() == T0 + timedelta(seconds=181)
        clock.set_clock(lambda: T0.replace(microsecond=999999))
        assert clock.now().microsecond == 0
    finally:
        clock.reset_clock()
    assert clock.now().year >= 2026
    assert clock.iso(T0) == "2030-01-01T12:00:00Z" and clock.iso(None) is None


# --------------------------------------------- device policy (pure function)
def device_row(**over):
    row = {"active_user_id": None, "last_user_id": None, "cooldown_until": None, "last_seen_at": T0}
    row.update(over)
    return row


def decide(row, user, now=T0):
    return device_service.evaluate_login(row, user, now, 180, 7200)


def test_fresh_device_allows_login_and_binds_user():
    decision, updates, events = decide(device_row(), 1)
    assert decision.allowed and updates == {"active_user_id": 1, "last_user_id": 1, "cooldown_until": None}
    assert events == [("login", 1, None)]


def test_other_student_blocked_during_cooldown_with_remaining_time():
    row = device_row(last_user_id=1, cooldown_until=T0 + timedelta(seconds=180))
    decision, updates, events = decide(row, 2, T0 + timedelta(seconds=30))
    assert not decision.allowed and decision.code == "device_cooldown" and decision.retry_after == 150
    assert updates == {} and events[0][0] == "cooldown_blocked"
    assert "student" not in decision.message.lower() and "1" not in decision.message.replace("150", "")


def test_same_student_may_log_back_in_during_cooldown_and_clears_it():
    row = device_row(last_user_id=1, cooldown_until=T0 + timedelta(seconds=180))
    decision, updates, _ = decide(row, 1, T0 + timedelta(seconds=10))
    assert decision.allowed and updates["cooldown_until"] is None


def test_other_student_allowed_once_cooldown_has_passed():
    row = device_row(last_user_id=1, cooldown_until=T0 + timedelta(seconds=180))
    assert decide(row, 2, T0 + timedelta(seconds=180))[0].allowed       # boundary: until > now is False
    assert decide(row, 2, T0 + timedelta(seconds=181))[0].allowed


def test_account_switch_while_another_student_is_active_starts_cooldown_and_blocks():
    decision, updates, events = decide(device_row(active_user_id=1, last_user_id=1), 2)
    assert not decision.allowed and decision.retry_after == 180
    assert updates == {"active_user_id": None, "last_user_id": 1, "cooldown_until": T0 + timedelta(seconds=180)}
    assert [e[0] for e in events] == ["account_switch", "cooldown_started"]
    assert events[1][1] == 1                                            # cooldown belongs to the previous student


def test_stale_active_user_does_not_block_forever():
    row = device_row(active_user_id=1, last_user_id=1, last_seen_at=T0 - timedelta(hours=3))
    assert decide(row, 2)[0].allowed


def test_remaining_seconds_round_up():
    row = device_row(last_user_id=1, cooldown_until=T0 + timedelta(seconds=10))
    assert decide(row, 2, T0 + timedelta(milliseconds=500))[0].retry_after == 10


# ------------------------------------------------------- start-request validation
def test_start_request_defaults_and_ranges(unit_app):
    with unit_app.app_context():
        cleaned = session_service.validate_start_request({"assignment_id": 3})
        assert cleaned["qr_validity_seconds"] == 120 and cleaned["geofence_enabled"] is True
        assert cleaned["radius_m"] == 50 and cleaned["min_accuracy_m"] == 50
        for bad, field in (({"assignment_id": 3, "qr_validity_seconds": 5}, "qr_validity_seconds"),
                           ({"assignment_id": 3, "duration_minutes": 0}, "duration_minutes"),
                           ({"assignment_id": 3, "radius_m": 5000}, "radius_m"),
                           ({"assignment_id": 3, "min_accuracy_m": "abc"}, "min_accuracy_m"),
                           ({"assignment_id": True}, "assignment_id"),
                           ({}, "assignment_id"),
                           ({"assignment_id": 3, "latitude": 95, "longitude": 10}, "latitude"),
                           ({"assignment_id": 3, "geofence_enabled": "yes"}, "geofence_enabled")):
            with pytest.raises(ServiceError) as info:
                session_service.validate_start_request(bad)
            assert field in info.value.fields, bad


def test_qr_validity_and_session_length_are_independent_settings(unit_app):
    with unit_app.app_context():
        cleaned = session_service.validate_start_request(
            {"assignment_id": 1, "qr_validity_seconds": 30, "duration_minutes": 90})
        assert (cleaned["qr_validity_seconds"], cleaned["duration_minutes"]) == (30, 90)


# --------------------------------------------------------------- reports
def test_attendance_percentage_definition():
    assert report_service.percent(0, 0) is None
    assert report_service.percent(3, 4) == 75.0 and report_service.percent(1, 3) == 33.3


def test_csv_export_neutralises_spreadsheet_formulas():
    row = {"session_id": 1, "started_at": T0, "subject_code": "AD301", "subject_name": "DS",
           "class_name": "SE", "section": "A", "full_name": "=HYPERLINK(\"http://x\")",
           "roll_number": "+1", "marked_at": T0, "distance_m": None, "gps_accuracy_m": 5}
    lines = report_service.to_csv([row]).splitlines()
    assert lines[0].startswith("Session ID,") and "'=HYPERLINK" in lines[1] and ",'+1," in lines[1]


# ----------------------------------------------- HTTP-level checks (no DB used)
def test_api_requires_authentication_with_json_envelope(unit_app):
    client = unit_app.test_client()
    for path in ("/api/student/attendance", "/api/device/status", "/api/teacher/attendance",
                 "/api/teacher/subjects", "/api/teacher/attendance-session/active"):
        response = client.get(path)
        body = response.get_json()
        assert response.status_code == 401, path
        assert body["ok"] is False and body["error"]["code"] == "authentication_required"


def test_login_page_issues_http_only_device_cookie_only_when_needed(unit_app):
    client = unit_app.test_client()
    first = client.get("/login").headers.getlist("Set-Cookie")
    cookie = next(c for c in first if c.startswith("nexora_device="))
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Max-Age=" in cookie
    assert not any(c.startswith("nexora_device=") for c in client.get("/login").headers.getlist("Set-Cookie"))
    client.set_cookie("nexora_device", "not-a-valid-token")
    assert any(c.startswith("nexora_device=") for c in client.get("/login").headers.getlist("Set-Cookie"))


def test_navigation_links_for_phase3_pages_are_live(unit_app):
    from app.navigation import build_nav
    with unit_app.test_request_context("/"):
        student = {i["label"]: i for i in build_nav("student")}
        teacher = {i["label"]: i for i in build_nav("teacher")}
    assert student["Scan QR"]["available"] and student["My Attendance"]["available"]
    assert teacher["Attendance Sessions"]["available"] and student["Academic Results"]["available"]
