r"""Insert FICTIONAL demonstration data. Safe to run once on an empty database.

  python scripts\seed_demo.py           seeds DB_NAME
  python scripts\seed_demo.py --test    seeds TEST_DB_NAME

All people, emails (@nexora.demo), roll numbers and coordinates are made up.
Every demo account uses the same password, printed at the end. Never use
this data or password in a real deployment.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app                                   # noqa: E402
from app.config import Config, TestConfig                    # noqa: E402
from app.db import execute, query_one, transaction          # noqa: E402
from app.services.auth_service import hash_password         # noqa: E402

DEMO_PASSWORD = "Nexora@2026"
ADMIN_EMAIL = "admin@nexora.demo"

DEPARTMENTS = [("AIDS", "Artificial Intelligence and Data Science"),
               ("CSE", "Computer Science and Engineering"),
               ("MECH", "Mechanical Engineering")]

SEMESTERS = [  # (year label, number, name, start, end, is_current)
    ("2025-26", 1, "Semester 1", "2025-06-16", "2025-11-30", 0),
    ("2025-26", 2, "Semester 2", "2025-12-15", "2026-05-15", 0),
    ("2026-27", 3, "Semester 3", "2026-06-15", "2026-11-30", 1),
    ("2026-27", 4, "Semester 4", "2026-12-15", "2027-05-15", 0),
]

SUBJECTS = [  # Semester 3, AI&DS
    ("AD301", "Data Structures", 4), ("AD302", "Database Management Systems", 4),
    ("AD303", "Discrete Mathematics", 3), ("AD304", "Object Oriented Programming", 3),
    ("AD305", "Statistics for Data Science", 3), ("AD306", "Python Programming Lab", 2),
]

TEACHERS = [("asha.verma@nexora.demo", "Prof. Asha Verma", "EMP-1001", ["AD301", "AD302", "AD303"]),
            ("rohan.iyer@nexora.demo", "Prof. Rohan Iyer", "EMP-1002", ["AD304", "AD305", "AD306"])]

STUDENTS = [("aarav.sharma@nexora.demo", "Aarav Sharma", "AIDS2026-001"),
            ("diya.patel@nexora.demo", "Diya Patel", "AIDS2026-002"),
            ("kabir.singh@nexora.demo", "Kabir Singh", "AIDS2026-003"),
            ("meera.nair@nexora.demo", "Meera Nair", "AIDS2026-004"),
            ("ishaan.gupta@nexora.demo", "Ishaan Gupta", "AIDS2026-005")]

PENDING_STUDENT = ("zoya.khan@nexora.demo", "Zoya Khan", "AIDS2026-006")

# A SAMPLE 10-point scheme. Edit it to match your institution; nothing in
# the application assumes these particular bands.
GRADE_RULES = [("O", 90, 100, 10), ("A+", 80, 89.99, 9), ("A", 70, 79.99, 8), ("B+", 60, 69.99, 7),
               ("B", 50, 59.99, 6), ("C", 45, 49.99, 5), ("P", 40, 44.99, 4), ("F", 0, 39.99, 0)]


def seed():
    if query_one("SELECT id FROM departments LIMIT 1"):
        print("Demo data is already present; nothing changed.")
        return
    pw_hash = hash_password(DEMO_PASSWORD)     # one hash reused: demo accounts only
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    def ins(sql, params):
        return execute(sql, params).lastrowid

    def add_user(email, name, role, status="active", approved_by=None):
        return ins("INSERT INTO users (email, password_hash, full_name, role, status, approved_by, approved_at) "
                   "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                   (email, pw_hash, name, role, status, approved_by, now if approved_by else None))

    with transaction():
        dept = {code: ins("INSERT INTO departments (code, name) VALUES (%s, %s)", (code, name))
                for code, name in DEPARTMENTS}

        years = {}
        for label, start, end, current in (("2025-26", "2025-06-16", "2026-05-31", 0),
                                           ("2026-27", "2026-06-15", "2027-05-31", 1)):
            years[label] = ins("INSERT INTO academic_years (label, start_date, end_date, is_current) "
                               "VALUES (%s, %s, %s, %s)", (label, start, end, current))
        sem = {}
        for label, number, name, start, end, current in SEMESTERS:
            sem[number] = ins("INSERT INTO semesters (academic_year_id, semester_number, name, start_date, "
                              "end_date, is_current) VALUES (%s, %s, %s, %s, %s, %s)",
                              (years[label], number, name, start, end, current))

        class_id = ins("INSERT INTO classes (department_id, academic_year_id, name, section) "
                       "VALUES (%s, %s, %s, %s)", (dept["AIDS"], years["2026-27"], "SE AI&DS", "A"))
        subject = {code: ins("INSERT INTO subjects (code, name, department_id, semester_number, credits) "
                             "VALUES (%s, %s, %s, 3, %s)", (code, name, dept["AIDS"], credits))
                   for code, name, credits in SUBJECTS}

        # Placeholder coordinates: edit to your real classroom before using geofencing.
        for room in ("Room A-101", "Room A-102"):
            ins("INSERT INTO classrooms (name, building, latitude, longitude, default_radius_m) "
                "VALUES (%s, %s, 20.000000, 78.000000, 50)", (room, "Main Block"))

        existing_admin = query_one(
            "SELECT id FROM users WHERE role = 'admin' AND status = 'active' ORDER BY id LIMIT 1"
        )
        admin_id = (
            existing_admin["id"]
            if existing_admin
            else add_user(ADMIN_EMAIL, "Demo Administrator", "admin", approved_by=None)
        )

        for email, name, code, subject_codes in TEACHERS:
            uid = add_user(email, name, "teacher", approved_by=admin_id)
            ins("INSERT INTO teacher_profiles (user_id, employee_code, department_id, designation) "
                "VALUES (%s, %s, %s, 'Assistant Professor')", (uid, code, dept["AIDS"]))
            for sc in subject_codes:
                ins("INSERT INTO teacher_subjects (teacher_id, subject_id, class_id, semester_id) "
                    "VALUES (%s, %s, %s, %s)", (uid, subject[sc], class_id, sem[3]))

        for email, name, roll in STUDENTS:
            uid = add_user(email, name, "student", approved_by=admin_id)
            ins("INSERT INTO student_profiles (user_id, roll_number, department_id, class_id, "
                "current_semester_id, admission_year) VALUES (%s, %s, %s, %s, %s, 2025)",
                (uid, roll, dept["AIDS"], class_id, sem[3]))
            for sid in subject.values():
                ins("INSERT INTO enrollments (student_id, subject_id, class_id, semester_id) "
                    "VALUES (%s, %s, %s, %s)", (uid, sid, class_id, sem[3]))

        email, name, roll = PENDING_STUDENT
        uid = add_user(email, name, "student", status="pending")
        ins("INSERT INTO student_profiles (user_id, roll_number, department_id) VALUES (%s, %s, %s)",
            (uid, roll, dept["AIDS"]))

        scheme = ins("INSERT INTO grading_schemes (name, description, is_default) VALUES (%s, %s, 1)",
                     ("Sample 10-point scheme", "Demonstration grading bands. Edit to match your institution."))
        for label, low, high, point in GRADE_RULES:
            ins("INSERT INTO grading_scheme_rules (scheme_id, grade_label, min_percent, max_percent, grade_point) "
                "VALUES (%s, %s, %s, %s, %s)", (scheme, label, low, high, point))

    print("Demo data created. All demo accounts use the password:", DEMO_PASSWORD)
    print(f"  Admin    : {ADMIN_EMAIL}")
    for email, name, *_ in TEACHERS:
        print(f"  Teacher  : {email}  ({name})")
    for email, name, _ in STUDENTS:
        print(f"  Student  : {email}  ({name})")
    print(f"  Pending  : {PENDING_STUDENT[0]}  (cannot sign in until the admin approves it)")


def main():
    parser = argparse.ArgumentParser(description="Seed fictional NEXORA demo data.")
    parser.add_argument("--test", action="store_true", help="seed the test database")
    args = parser.parse_args()
    app = create_app(TestConfig if args.test else Config)
    with app.app_context():
        seed()


if __name__ == "__main__":
    main()
