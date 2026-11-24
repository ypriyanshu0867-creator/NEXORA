r"""Fictional demo attendance history so the teacher reports and corrections have something to show.

  python scripts\seed_phase5.py          (after seed_demo.py and migrate.py)
  python scripts\seed_phase5.py --test

Creates three FINISHED attendance sessions for Prof. Asha Verma's first assignment (AD301) with different
attendees, so student-wise percentages differ and you can try a correction. Runs only if that assignment
has no sessions yet. Everything is made up.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app                                   # noqa: E402
from app.config import Config, TestConfig                    # noqa: E402
from app.db import execute, query_all, query_one, transaction   # noqa: E402


def seed():
    teacher = query_one("SELECT id FROM users WHERE email = 'asha.verma@nexora.demo'")
    if not teacher:
        sys.exit("Run  python scripts\\seed_demo.py  first.")
    assignment = query_one("SELECT ts.id, ts.subject_id, ts.class_id, ts.semester_id FROM teacher_subjects ts "
                           "JOIN subjects s ON s.id = ts.subject_id WHERE ts.teacher_id = %s AND s.code = 'AD301'", (teacher["id"],))
    if not assignment:
        sys.exit("Prof. Asha Verma has no AD301 assignment (run seed_demo.py).")
    if query_one("SELECT id FROM attendance_sessions WHERE assignment_id = %s LIMIT 1", (assignment["id"],)):
        print("AD301 already has sessions; nothing changed.")
        return
    students = [r["student_id"] for r in query_all(
        "SELECT student_id FROM enrollments WHERE subject_id = %s AND class_id = %s AND semester_id = %s AND status = 'active' "
        "ORDER BY student_id", (assignment["subject_id"], assignment["class_id"], assignment["semester_id"]))]
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    plans = [(9, students), (6, students[:3]), (2, students[:2])]          # days ago, who attended
    with transaction():
        for days_ago, attendees in plans:
            start = now - timedelta(days=days_ago, hours=2)
            sid = execute("INSERT INTO attendance_sessions (assignment_id, status, started_at, session_ends_at, closed_at, "
                          "qr_validity_seconds, geofence_enabled) VALUES (%s, 'closed', %s, %s, %s, 120, 0)",
                          (assignment["id"], start, start + timedelta(minutes=10), start + timedelta(minutes=10))).lastrowid
            for n, student in enumerate(attendees):
                execute("INSERT INTO attendance_records (session_id, student_id, marked_at) VALUES (%s, %s, %s)",
                        (sid, student, start + timedelta(minutes=1 + n)))
    print(f"Created {len(plans)} finished AD301 sessions for {len(students)} enrolled students.")
    print("Sign in as asha.verma@nexora.demo to see reports, then open a session to correct attendance.")


def main():
    parser = argparse.ArgumentParser(description="Seed demo attendance history.")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    app = create_app(TestConfig if args.test else Config)
    with app.app_context():
        seed()


if __name__ == "__main__":
    main()
