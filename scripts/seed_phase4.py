r"""Fictional Phase 4 demonstration data (run AFTER seed_demo.py and migrate.py).

  python scripts\seed_phase4.py           seeds DB_NAME
  python scripts\seed_phase4.py --test    seeds TEST_DB_NAME

Adds: Semester 1-2 subjects with PUBLISHED results (and a repeated subject
for one student), unpublished Semester 3 results, library books and loans,
syllabus units, three small demo PDFs, notices (including expired, unpublished,
targeted and attached ones) and fee records with confirmed / pending / failed
payments. Everything is made up. Safe to run once; it detects its own data.
"""
import argparse
import hashlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app                                   # noqa: E402
from app.config import Config, TestConfig                    # noqa: E402
from app.db import execute, query_all, query_one, transaction   # noqa: E402

MARKER = "Welcome to the new semester (demo)"
STUDENT_EMAILS = ["aarav.sharma", "diya.patel", "kabir.singh", "meera.nair", "ishaan.gupta"]

SUBJECTS = {1: [("AD101", "Programming Fundamentals", 4), ("AD102", "Engineering Mathematics I", 4),
                ("AD103", "Digital Electronics", 3), ("AD104", "Communication Skills", 2)],
            2: [("AD201", "Data Handling with Python", 4), ("AD202", "Engineering Mathematics II", 4),
                ("AD203", "Computer Organisation", 3), ("AD204", "Environmental Studies", 2)]}

BOOKS = [  # title, author, isbn, category, publisher, year, shelf, copies
    ("Foundations of Programming (Demo Edition)", "R. Sample", "978-1-00000-001-1", "Programming", "Demo Press", 2021, "A-01", 3),
    ("Data Structures Step by Step (Demo)", "S. Example", "978-1-00000-002-8", "Programming", "Demo Press", 2020, "A-02", 2),
    ("Practical Databases (Demo)", "T. Placeholder", "978-1-00000-003-5", "Databases", "Sample Books", 2019, "B-01", 2),
    ("Discrete Mathematics Made Clear (Demo)", "U. Illustrative", "978-1-00000-004-2", "Mathematics", "Sample Books", 2018, "C-01", 3),
    ("Statistics for Data Science (Demo)", "V. Mockup", "978-1-00000-005-9", "Data Science", "Demo Press", 2022, "D-01", 2),
    ("Introduction to Machine Learning (Demo)", "W. Specimen", "978-1-00000-006-6", "Data Science", "Demo Press", 2023, "D-02", 1),
    ("Digital Electronics Basics (Demo)", "X. Fictional", "978-1-00000-007-3", "Electronics", "Sample Books", 2017, "E-01", 2),
    ("Python for Everyone (Demo)", "Y. Invented", "978-1-00000-008-0", "Programming", "Demo Press", 2022, "A-03", 4),
    ("Technical Writing Guide (Demo)", "Z. Imaginary", "978-1-00000-009-7", "General", "Sample Books", 2016, "G-01", 2),
    ("Operating Systems Concepts (Demo)", "A. Hypothetical", "978-1-00000-010-3", "Computer Science", "Demo Press", 2020, "F-01", 1),
]


def pdf_bytes(text):
    """A tiny valid-looking PDF. Demonstration placeholder, not real course material."""
    body = (f"BT /F1 16 Tf 20 70 Td ({text}) Tj ET").encode("latin-1")
    return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 360 144]/Contents 4 0 R"
            b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
            + b"4 0 obj<</Length " + str(len(body)).encode() + b">>stream\n" + body + b"\nendstream endobj\n"
            b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF\n")


def store_pdf(app, subdir, text):
    folder = Path(app.config["UPLOAD_FOLDER"]) / subdir
    folder.mkdir(parents=True, exist_ok=True)
    data = pdf_bytes(text)
    name = uuid.uuid4().hex + ".pdf"
    (folder / name).write_bytes(data)
    return name, len(data), hashlib.sha256(data).hexdigest()


def grade_for(rules, marks):
    for r in rules:                                                  # rules sorted by min_percent desc
        if Decimal(r["min_percent"]) <= Decimal(marks) <= Decimal(r["max_percent"]):
            return r
    return rules[-1]


def seed(app):
    if not query_one("SELECT id FROM users WHERE email = 'admin@nexora.demo'"):
        sys.exit("Run  python scripts\\seed_demo.py  first.")
    if query_one("SELECT id FROM notices WHERE title = %s", (MARKER,)):
        print("Phase 4 demo data is already present; nothing changed.")
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    today = now.date()
    admin = query_one("SELECT id FROM users WHERE email = 'admin@nexora.demo'")["id"]
    asha = query_one("SELECT id FROM users WHERE email = 'asha.verma@nexora.demo'")["id"]
    students = [query_one("SELECT id FROM users WHERE email = %s", (e + "@nexora.demo",))["id"] for e in STUDENT_EMAILS]
    aids = query_one("SELECT id FROM departments WHERE code = 'AIDS'")["id"]
    cse = query_one("SELECT id FROM departments WHERE code = 'CSE'")["id"]
    sem = {r["semester_number"]: r["id"] for r in query_all("SELECT id, semester_number FROM semesters")}
    ay_cur = query_one("SELECT id FROM academic_years WHERE is_current = 1")["id"]
    scheme = query_one("SELECT id FROM grading_schemes WHERE is_default = 1")["id"]
    rules = query_all("SELECT grade_label, min_percent, max_percent, grade_point, is_pass FROM grading_scheme_rules "
                      "WHERE scheme_id = %s ORDER BY min_percent DESC", (scheme,))
    sid = {}

    with transaction():
        execute("UPDATE grading_scheme_rules SET is_pass = IF(grade_point = 0, 0, 1) WHERE scheme_id = %s", (scheme,))
        for number, rows in SUBJECTS.items():
            for code, name, credits in rows:
                sid[code] = execute("INSERT INTO subjects (code, name, department_id, semester_number, credits) "
                                    "VALUES (%s, %s, %s, %s, %s)", (code, name, aids, number, credits)).lastrowid
        for r in query_all("SELECT id, code, credits FROM subjects WHERE semester_number = 3 AND department_id = %s", (aids,)):
            sid[r["code"]] = r["id"]
        credits_of = {r["id"]: r["credits"] for r in query_all("SELECT id, credits FROM subjects")}

        def add_result(student, subject, semester, marks, published, when):
            rule = grade_for(rules, marks)
            execute("INSERT INTO academic_results (student_id, subject_id, semester_id, scheme_id, marks_obtained, "
                    "max_marks, grade_label, grade_point, credits_snapshot, is_published, entered_by, entered_at) "
                    "VALUES (%s, %s, %s, %s, %s, 100, %s, %s, %s, %s, %s, %s)",
                    (student, subject, semester, scheme, marks, rule["grade_label"], rule["grade_point"],
                     credits_of[subject], published, admin, when))

        for si, student in enumerate(students):
            for number, when in ((1, datetime(2026, 1, 10, 10, 0)), (2, datetime(2026, 6, 20, 10, 0))):
                for ji, (code, _, _) in enumerate(SUBJECTS[number]):
                    marks = 45 + ((si * 17 + ji * 11 + number * 7) % 51)
                    if si == 2 and code == "AD103":
                        marks = 32                                   # Kabir fails Digital Electronics...
                    add_result(student, sid[code], sem[number], marks, 1, when)
            for ji, code in enumerate(["AD301", "AD302", "AD303", "AD304", "AD305", "AD306"]):
                add_result(student, sid[code], sem[3], 55 + ((si * 5 + ji * 9) % 40), 0, datetime(2026, 10, 20, 10, 0))
        add_result(students[2], sid["AD103"], sem[3], 55, 1, datetime(2026, 9, 5, 10, 0))   # ...and repeats it later

        # ---- library
        book_ids = []
        for title, author, isbn, cat, pub, year, shelf, copies in BOOKS:
            book_ids.append(execute("INSERT INTO library_books (isbn, title, author, category, publisher, publication_year, "
                                    "shelf_location, total_copies, available_copies) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                                    (isbn, title, author, cat, pub, year, shelf, copies, copies)).lastrowid)
        loans = [(0, 0, 18, 4, None, 0), (0, 1, 8, -6, None, 0), (0, 2, 40, 26, 29, 0),
                 (1, 3, 13, -1, None, 0), (2, 4, 70, 56, 51, "25.00")]
        for si, bi, issued_ago, due_in_days_ago, returned_ago, fine in loans:
            issued = today - timedelta(days=issued_ago)
            due = today - timedelta(days=due_in_days_ago)
            returned = None if returned_ago is None else today - timedelta(days=returned_ago)
            execute("INSERT INTO library_issues (book_id, student_id, issued_on, due_on, returned_on, fine_amount, issued_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)", (book_ids[bi], students[si], issued, due, returned, fine, admin))
            if returned is None:
                execute("UPDATE library_books SET available_copies = available_copies - 1 WHERE id = %s", (book_ids[bi],))

        # ---- syllabus and materials
        units = {"AD301": [(1, "Arrays and Linked Lists", "Array operations, singly and doubly linked lists, applications.", 10,
                            "Implement list operations; compare array and list trade-offs.", "Demo Reference Text, chapters 1-3"),
                           (2, "Stacks and Queues", "Stack ADT, expression evaluation, queue variants, circular queue.", 8,
                            "Apply stacks and queues to problems.", "Demo Reference Text, chapters 4-5"),
                           (3, "Trees", "Binary trees, traversals, binary search trees, balancing ideas.", 12,
                            "Implement and analyse tree traversals.", "Demo Reference Text, chapter 6")],
                 "AD302": [(1, "Relational Model", "Tables, keys, relational algebra, ER modelling.", 9, None, None),
                           (2, "SQL", "DDL, DML, joins, aggregation, views, transactions.", 12, "Write non-trivial SQL queries.", None)],
                 "AD101": [(1, "Programming Basics", "Variables, control flow, functions.", 10, None, None),
                           (2, "Problem Solving", "Decomposition, testing and debugging.", 8, None, None)]}
        for code, rows in units.items():
            for number, title, topics, hours, outcomes, refs in rows:
                execute("INSERT INTO syllabus (subject_id, unit_number, title, topics, lecture_hours, learning_outcomes, "
                        "reference_books, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (sid[code], number, title, topics, hours, outcomes, refs, now))
        for code, title, published in (("AD301", "Data Structures: Unit 1 notes (demo PDF)", 1),
                                       ("AD302", "DBMS: SQL practice sheet (demo PDF)", 1),
                                       ("AD101", "Programming Fundamentals: lab manual (demo PDF)", 1),
                                       ("AD301", "Data Structures: DRAFT notes (unpublished)", 0)):
            stored, size, digest = store_pdf(app, "materials", f"NEXORA demo - {code}")
            execute("INSERT INTO learning_materials (subject_id, title, original_filename, stored_filename, size_bytes, "
                    "sha256, uploaded_by, uploaded_at, is_published) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (sid[code], title, f"{code.lower()}_notes.pdf", stored, size, digest, asha, now, published))

        # ---- notices
        att_name, _, _ = store_pdf(app, "notices", "NEXORA demo - exam hall rules")
        def notice(title, body, category, *, audience="all", dept=None, sem_no=None, published_days=1, expires_days=None,
                   deadline_days=None, pinned=0, active=1, attach=False):
            execute("INSERT INTO notices (title, body, category, audience, department_id, semester_number, deadline_at, "
                    "is_active, created_by, created_at, published_at, expires_at, is_pinned, attachment_stored_name, "
                    "attachment_original_name) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (title, body, category, audience, dept, sem_no,
                     None if deadline_days is None else now + timedelta(days=deadline_days), active, admin,
                     now - timedelta(days=published_days), now - timedelta(days=published_days),
                     None if expires_days is None else now + timedelta(days=expires_days), pinned,
                     att_name if attach else None, "exam_hall_rules.pdf" if attach else None))
        notice(MARKER, "Classes resume as per the timetable. Please check your attendance regularly.", "general", published_days=10, pinned=1)
        notice("Mid-semester examination schedule", "Mid-semester examinations begin in two weeks. The detailed schedule is attached.",
               "exam", audience="student", dept=aids, published_days=3, deadline_days=14, attach=True)
        notice("Festival holidays", "The college will remain closed for the festival holidays.", "holiday", published_days=5, expires_days=20)
        notice("Timetable change: AD302 lab", "The AD302 lab batch timings have changed for Semester 3.", "timetable", sem_no=3, published_days=2)
        notice("Assignment 2 submission deadline", "Submit Assignment 2 through your subject teacher.", "assignment",
               audience="student", published_days=4, deadline_days=5)
        notice("Semester 2 results published", "Semester 2 results are now available on your Academic Results page.", "result",
               published_days=120, expires_days=-1)
        notice("Fee payment reminder", "Pending fees must be cleared before the due date shown on your Fees page.", "fee", published_days=6)
        notice("Library new arrivals", "New demonstration titles have been added to the library catalogue.", "library", published_days=7)
        notice("CSE department guest seminar", "Open to Computer Science and Engineering students only.", "event", dept=cse, published_days=2)
        notice("Semester 5 elective choices", "Elective selection for Semester 5 students.", "general", sem_no=5, published_days=2)
        notice("DRAFT: orientation plan", "Not yet published.", "general", published_days=1, active=0)

        # ---- fees
        def structure(name, category, sem_no, total, due_days):
            return execute("INSERT INTO fee_structures (name, department_id, academic_year_id, total_amount, due_date, category, "
                           "semester_number) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                           (name, aids, ay_cur, total, today + timedelta(days=due_days), category, sem_no)).lastrowid
        tuition = structure("Tuition Fee - Semester 3", "Tuition", 3, "60000.00", -30)
        exam = structure("Examination Fee - Semester 3", "Examination", 3, "3000.00", 30)
        deposit = structure("Library Deposit", "Library", None, "1000.00", 60)

        def record(student, struct, due, due_days, status, remarks, payments):
            confirmed = sum((Decimal(a) for a, _, s in payments if s == "confirmed"), Decimal("0.00"))
            rid = execute("INSERT INTO student_fee_records (student_id, fee_structure_id, amount_due, amount_paid, due_date, "
                          "status, remarks) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                          (student, struct, due, confirmed, today + timedelta(days=due_days), status, remarks)).lastrowid
            for n, (amount, method, tx_status) in enumerate(payments):
                execute("INSERT INTO fee_transactions (fee_record_id, amount, method, reference_no, paid_on, note, recorded_by, status) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (rid, amount, method, f"DEMO-{rid}-{n + 1}", today - timedelta(days=20 - n * 5), None, admin, tx_status))
        a, d, k, m, i = students
        record(a, tuition, "60000.00", -30, "paid", None, [("40000.00", "bank_transfer", "confirmed"), ("20000.00", "cash", "confirmed")])
        record(a, exam, "3000.00", 30, "unpaid", "Payment recorded, awaiting bank confirmation.", [("3000.00", "bank_transfer", "pending")])
        record(a, deposit, "1000.00", 60, "unpaid", None, [])
        record(d, tuition, "60000.00", -30, "partial", None, [("30000.00", "bank_transfer", "confirmed"), ("10000.00", "cheque", "pending")])
        record(d, exam, "3000.00", 30, "unpaid", None, [])
        record(k, tuition, "60000.00", -30, "unpaid", None, [])
        record(k, exam, "3000.00", 30, "waived", "Fee waived under a demonstration scholarship.", [])
        record(m, tuition, "60000.00", -30, "paid", None, [("60000.00", "bank_transfer", "confirmed")])
        record(m, exam, "3000.00", 30, "paid", None, [("3000.00", "cash", "confirmed")])
        record(i, tuition, "60000.00", -30, "unpaid", "Payment attempt failed at the bank; please retry.", [("60000.00", "bank_transfer", "failed")])

    print("Phase 4 demo data created.")
    print("  Aarav Sharma : published Sem 1-2 results, 2 books on loan (1 overdue), paid tuition, pending exam fee")
    print("  Diya Patel   : partially paid tuition (overdue) with a pending cheque")
    print("  Kabir Singh  : failed + repeated AD103 (see the CGPA repeat policy), waived exam fee")
    print("  Ishaan Gupta : failed payment attempt (not counted as paid)")
    print("  Semester 3 results exist but are UNPUBLISHED, so students see 'not published yet'.")


def main():
    parser = argparse.ArgumentParser(description="Seed fictional Phase 4 demo data.")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    app = create_app(TestConfig if args.test else Config)
    with app.app_context():
        seed(app)


if __name__ == "__main__":
    main()
