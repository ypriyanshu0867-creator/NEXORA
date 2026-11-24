# Phase 4: student portal

Pages (all need a student login unless noted): `/student/` dashboard, `/attendance/history`,
`/academics/results`, `/academics/calculator`, `/library/` (also teachers/admins), `/library/my-books`,
`/syllabus/` (also teachers/admins), `/notices/` (also teachers/admins), `/fees/`, `/student/profile`.
JSON API under `/api/student/...` (dashboard, profile, results, academic-history, library, library/books,
library/issues, syllabus, notices, fees, calculator-data, sgpa/calculate, cgpa/calculate).
Every route takes the student from the login; none accepts a student id.

## Database changes: `database/migrations/002_phase4_student_portal.sql`
| Change | Why |
|---|---|
| `grading_scheme_rules.is_pass` (rows with 0 grade points set to fail) | Pass/fail status per grade band |
| `library_books.publisher`, `publication_year` | Book details |
| `syllabus.learning_outcomes`, `reference_books`, `updated_at` | Unit details and publication date |
| `learning_materials.is_published` | Unpublished files are never offered |
| `notices`: 3 more categories, `semester_number`, `published_at`, `expires_at`, `is_pinned`, attachment name columns, visibility index | Targeting, expiry, pinning, attachments |
| `fee_structures.category`, `semester_number` | Fee category and semester |
| `student_fee_records.status` gains `waived`, new `remarks` | Waivers and administrator remarks |
| `fee_transactions.status` (confirmed / pending / failed) | Only confirmed payments count |

Apply with `python scripts\migrate.py` (existing data is kept). Rollback guidance:
`database\migrations\rollback\002_phase4_student_portal.rollback.sql` (manual; read its warnings first).

## Policies (explicit, replaceable)
* **Grading:** the default grading scheme in the database (`grading_schemes.is_default = 1`) defines grades, grade
  points and pass/fail. With none configured, calculators say the institution must configure one. The seeded
  "Sample 10-point scheme" is a demonstration, not any university's rules.
* **SGPA** = Σ(credit × grade point) ÷ Σ(credits) for one semester's published results. 0-credit subjects have no
  weight; failed subjects count with their grade points.
* **CGPA** = the same formula over all published results after the **repeat policy** (`CGPA_REPEAT_POLICY` in `.env`):
  `latest` (default) = the most recent attempt of a subject replaces earlier ones; `best` = highest grade points.
  Semester SGPAs are never averaged.
* **Official vs estimate:** only *published* results are shown to students (unpublished marks are never revealed).
  The schema stores no separate official CGPA, so the CGPA on the results page is *calculated from official published
  results* and labelled so. Calculator output is an *estimate* unless it exactly reproduces published results.
* **Rounding:** `Decimal`, half-up, `GPA_DECIMALS` places (default 2). No floating-point in grades or money.
* **Fees:** Outstanding = assessed − **confirmed** payments, computed from transactions. Pending/failed ones are shown
  but never counted. Waived = nothing outstanding. Status: Paid, Partially paid, Pending, Overdue (due date passed and a
  balance remains), Waived. This is fee *tracking*; there is no payment gateway and no card/bank data is stored.
* **Library:** read-only for students. Fines shown are the recorded amount, or days overdue × `LIBRARY_FINE_PER_DAY`
  when that is set above 0.
* **Notices** are visible only if active, already published, addressed to the viewer's role, matching department
  (empty = all) and, for students, semester (empty = all). Expired ones are hidden by default and shown with
  "Include expired". Unpublished, other-department and other-semester notices answer 404.
* **Materials:** students may download published materials of their own department up to their current semester.
  Syllabus text of any semester in the department is readable.
* **Files:** the browser sends a database id only. The stored name is re-checked (`<32 hex>.pdf`), resolved inside
  `instance/uploads/<materials|notices>/`, and must start with `%PDF-`. The download name is sanitised. Failures all
  return the same 404 without paths.
* **Profile:** students can change only their phone number and password (existing hashing, policy and lockout).

## Integration points for Phase 5 (staff interfaces)
* Results entry: store `grade_label`, `grade_point`, `credits_snapshot`, `scheme_id`; publish with `is_published = 1`.
* Notices: write `instance/uploads/notices/<uuid4().hex>.pdf` and set `attachment_stored_name` / `attachment_original_name`.
* Materials: write `instance/uploads/materials/<uuid4().hex>.pdf` + a `learning_materials` row (size, sha256, `is_published`).
* Fees: insert `fee_transactions` and update `student_fee_records.amount_paid`/`status` in one transaction. The portal
  ignores the cached `amount_paid` and always sums confirmed transactions.
* Library issue/return: one transaction that adjusts `available_copies` (the schema CHECK stops it exceeding total).

## Known limitations
* Not run against MySQL or in a browser (see the report). Chart.js is not used; progress bars are plain CSS.
* No separate stored official SGPA/CGPA, no excused-absence handling, no library reservations.
* A student with no current semester set sees no semester-scoped notices and no materials until the administrator sets it.
* Bootstrap and fonts still load from a CDN without integrity hashes.

## Windows 11 demonstration (Command Prompt)
```
cd path\to\nexora
venv\Scripts\activate
python -m pip install -r requirements.txt
net start MySQL80
python scripts\migrate.py
python scripts\seed_demo.py
python scripts\seed_phase4.py
python run.py
```
(`net start` needs an Administrator prompt; skip it if MySQL is already running. `seed_demo.py` is needed once, before
`seed_phase4.py`; both say so if the data already exists. No new packages are required.)

Open http://localhost:5000 and sign in as `aarav.sharma@nexora.demo` / `Nexora@2026`:
1. **Dashboard** `/student/`: attendance, SGPA, CGPA, fee balance, books issued, notices, activity.
2. **Attendance** `/attendance/history` (after a teacher has run a session, see Phase 3).
3. **Results** `/academics/results`: Semesters 1 and 2 published; "Semester 3 not published yet".
4. **Calculator** `/academics/calculator`: pick Semester 1, choose grades, *Calculate SGPA*; then *official CGPA*.
5. **Library** `/library/`: search "python", filter a category; **My books** shows the loans, one overdue.
6. **Syllabus** `/syllabus/`: Semester 3, open AD301, download the demo PDF.
7. **Notices** `/notices/`: pinned first; tick "Include expired" to see the expired one; open the exam notice's attachment.
8. **Fees** `/fees/`: tuition paid; exam fee pending (not counted until confirmed).
9. **Access checks:** sign out, sign in as `diya.patel@nexora.demo` (wait 180 s, or use the same account). Her fees and
   loans differ from Aarav's. Try `/admin/` (403), `/syllabus/materials/99999/download` (404), and as a teacher
   (`asha.verma@nexora.demo`) open `/fees/` (403).

Tests
```
python scripts\init_db.py --test
python -m pytest tests\test_phase4_unit.py tests\test_phase4_integration.py -v
python -m pytest tests -v
```
