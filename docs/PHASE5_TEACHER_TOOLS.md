# Phase 5, part 1: teacher dashboard and management tools

Part 2 (the administrator panel: users, assignments, academic structure, classrooms, grading schemes, library,
fees, notice/syllabus administration, audit viewer and reports) is NOT built yet.

## Teacher pages (all teacher-only; foreign ids answer 404)
| Page | URL | What it does |
|---|---|---|
| Dashboard | `/teacher/` | Subjects, classes, sessions held, present today, average attendance, deadlines, active/recent sessions, activity, notices |
| My classes | `/teacher/classes`, `/teacher/classes/<assignment>` | Assigned subject+class list; student list with attendance % |
| Attendance sessions | `/teacher/attendance` | Phase 3 QR workflow (unchanged) |
| Session history | `/teacher/sessions`, `/teacher/sessions/<id>` | Filters (subject/class, state, dates, semester); records; **corrections** |
| Reports | `/teacher/reports` | Subject-wise and student-wise percentages; CSV |
| Results | `/teacher/results`, `/teacher/results/<assignment>` | Enter marks, save drafts, publish, export CSV |
| Syllabus & materials | `/teacher/syllabus`, `/teacher/syllabus/<subject>` | Units, learning outcomes, PDF upload, publish/hide, publication history |
| Notices | `/teacher/notices`, `/teacher/notices/new` | Class announcements with optional PDF attachment |

## Database changes: `database/migrations/003_phase5_teacher_tools.sql`
| Change | Why |
|---|---|
| New table `attendance_corrections` + `attendance_records.source` | Keep the original value, reason, teacher and time of every correction; mark records created by a correction |
| `syllabus.is_published` | Teachers can hide units; students see published units only |
| `learning_materials.description` | Material descriptions |
| `notices.class_id`, category `material` | Class-specific announcements and material announcements |

Apply with `python scripts\migrate.py`. Rollback guidance: `database\migrations\rollback\003_phase5_teacher_tools.rollback.sql`.

## Policies
* **Scope:** every query joins `teacher_subjects.teacher_id = you`. There is no route that lets a teacher change assignments,
  roles or departments.
* **Corrections:** only for a FINISHED session of your own class, with a written reason (5 to 255 characters). The record is
  added (`source = 'correction'`) or removed; the old values, reason, teacher and time go to `attendance_corrections` and an
  `audit_logs` entry. Reports and student percentages read the live records, so they update automatically. A finished session
  whose only attendee was corrected away still counts as held.
* **Results:** the grade and grade points are DERIVED on the server from the default grading scheme (highest band whose
  minimum is at most marks ÷ maximum × 100); the browser never supplies a grade. Marks must be 0 to the maximum (up to
  1000), at most two decimals. A save is all-or-nothing. Rows start as drafts, invisible to students; teachers may publish
  their own subjects unless `TEACHER_CAN_PUBLISH_RESULTS=0`. A published result can no longer be edited by a teacher. The
  credits and scheme are snapshotted, so later scheme changes do not rewrite history. Phase 4's SGPA/CGPA read the same rows.
* **Uploads:** `.pdf` name AND `%PDF-` content, size cap `MATERIAL_MAX_MB` (default 10), random server-side name, stored in
  `instance/uploads/`, never executed, only served as downloads through the Phase 4 safe-file route. PDFs may contain scripts;
  they are sent as attachments, never displayed by the site.
* **Notices:** a teacher addresses one of their own classes (optionally a semester they teach it), students of their own
  department. Allowed categories: general, exam, assignment, timetable, material. Holiday, event, fee, library and result notices
  stay administrative. Teachers edit only their own notices. Drafts and withdrawn notices are never shown to students.
* **Syllabus:** units are created, edited and hidden, never deleted, so history is preserved. Unit numbers are unique per subject.

## Audit log integrity (read this)
Every teacher action above writes `audit_logs` (actor, action, record, time, IP). The application never updates or deletes
audit rows. To make the DATABASE refuse changes too, run once with your MySQL admin login:
`python scripts\protect_audit_tables.py` (installs append-only triggers on `audit_logs` and `attendance_corrections`;
`--remove` undoes it). This is a separate step because on a default MySQL 8 server the app account cannot create triggers.
Limits: a database administrator can drop the triggers or edit rows, and anyone with file or backup access can alter data.
For stronger guarantees copy logs to write-once or external storage. Logs contain no passwords, hashes, tokens or sessions.

## Known limitations
* Not run against MySQL or in a browser. Administrator tools (part 2) are not built, so assignments, enrolments, grading
  schemes and classrooms are still set only through the seed scripts or SQL.
* Unpublishing a published result (with a reason) is planned for the administrator panel.
* Teachers cannot delete units, materials or notices (hide/withdraw instead).
* Times typed in forms are UTC.

## Windows 11 demonstration (Command Prompt)
```
cd path\to\nexora
venv\Scripts\activate
python -m pip install -r requirements.txt
net start MySQL80
python scripts\migrate.py
python scripts\seed_demo.py
python scripts\seed_phase5.py
python run.py
```
(`net start` needs an Administrator prompt, skip it if MySQL is running. `seed_demo.py` and `seed_phase5.py` say so if their data
already exists. Optional, once, with your MySQL admin login: `python scripts\protect_audit_tables.py`.)

Open http://localhost:5000 and sign in as `asha.verma@nexora.demo` / `Nexora@2026`:
1. **Dashboard** `/teacher/`: figures come from the three demo sessions created by `seed_phase5.py`.
2. **My classes**: open AD301, see per-student percentages; **Export attendance CSV**.
3. **Session history** → open the oldest session → under a student choose *Mark present* or *Mark absent*, give a reason.
   The correction appears under *Correction history*; the percentage on the class page changes.
4. **Results**: open AD301, enter marks (for example 85, 62, 91), *Save as draft*, then *Save and publish drafts*. Sign in as
   `aarav.sharma@nexora.demo` to see them on *Academic Results*; try editing a published mark as the teacher (refused).
5. **Syllabus & materials**: add a unit, hide it, upload a PDF, publish/hide it; check the student's *Syllabus* page.
6. **Notices**: *New notice*, choose class "SE A", *Save and publish*; the students of that class see it; *Withdraw* hides it.
7. **Starting attendance** with the QR works exactly as in Phase 3 (`docs\PHASE3_ATTENDANCE.md`).

Tests
```
python scripts\init_db.py --test
python -m pytest tests\test_phase5_unit.py tests\test_phase5_integration.py -v
python -m pytest tests -v
```
