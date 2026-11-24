# NEXORA Architecture (Phase 1)

Smart Attendance System Using QR Code with Anti-Proxy Verification.
Stack: Flask, MySQL 8.x (`mysql-connector-python`), Bootstrap 5, vanilla JS, Chart.js, pytest.

## 1. Folder tree

Files marked (P1) exist now. The rest are built in the phase shown.

```
nexora/
├── app/
│   ├── __init__.py              create_app() factory                      (P2)
│   ├── config.py                Config / TestConfig from environment      (P2)
│   ├── db.py                    connection pool, query helpers, transaction() (P2)
│   ├── extensions.py            CSRF instance                             (P2)
│   ├── security.py              decorators: login_required, role_required (P2)
│   ├── errors.py                404/403/500 handlers, no leaked details   (P8)
│   ├── routes/                  thin HTTP layer only
│   │   ├── auth.py              register, login, logout, pending approval (P2)
│   │   ├── attendance.py        QR scan/mark API, session control         (P3)
│   │   ├── students.py          student dashboard and pages               (P4)
│   │   ├── academics.py         results, SGPA/CGPA                        (P4)
│   │   ├── teachers.py          teacher dashboard, reports                (P5)
│   │   ├── admin.py             user/department/classroom management      (P6)
│   │   ├── library.py                                                     (P7)
│   │   ├── syllabus.py          syllabus + PDF upload/download            (P7)
│   │   ├── notices.py                                                     (P7)
│   │   └── fees.py                                                        (P7)
│   ├── services/                business logic, no Flask objects
│   │   ├── auth_service.py      hashing, approval, login rate limit       (P2)
│   │   ├── device_service.py    device registration + cooldown            (P3)
│   │   ├── geo_service.py       Haversine + accuracy/radius policy        (P3)
│   │   ├── qr_service.py        token create/hash/verify, QR image        (P3)
│   │   ├── attendance_service.py  start/close session, mark (transaction)(P3)
│   │   ├── audit_service.py     audit log writer                          (P3)
│   │   ├── academic_service.py  grading, SGPA, CGPA                       (P4)
│   │   ├── report_service.py    attendance reports, CSV export            (P5)
│   │   ├── admin_service.py                                               (P6)
│   │   ├── library_service.py / syllabus_service.py /
│   │   │   notice_service.py / fee_service.py                             (P7)
│   ├── templates/
│   │   ├── base.html, layouts/, components/ (toasts, cards, empty state)  (P2+)
│   │   ├── auth/, student/, teacher/, admin/, errors/                     (P2-P8)
│   ├── static/
│   │   ├── css/nexora.css       theme (#14213D, #3F6FC0, #F5F7FB)         (P2)
│   │   ├── js/                  app.js, scanner.js, geo.js, device.js,
│   │   │                        teacher-qr.js, charts.js                  (P2-P5)
│   │   └── img/
├── database/
│   ├── schema.sql               30 tables                                 (P1) ✔
│   └── seed.sql / scripts below                                          (P2)
├── scripts/
│   ├── init_db.py               create DB, run schema, optional --reset   (P2)
│   └── seed_demo.py             fictional demo data                       (P2)
├── instance/uploads/            PDFs, outside the static folder           (P7)
├── tests/                       pytest, dedicated nexora_test DB          (P9)
├── docs/
│   ├── ARCHITECTURE.md          this file                                 (P1) ✔
│   ├── SETUP_WINDOWS.md                                                   (P9)
│   └── SECURITY.md              honest limitations                        (P9)
├── .env.example                                                           (P1) ✔
├── .gitignore                                                             (P1) ✔
├── requirements.txt                                                       (P1) ✔
└── run.py                       dev server entry point                    (P2)
```

Layering rule: `routes` call `services`, `services` call `db`. Routes never
contain SQL; services never touch `request` or `session`.

## 2. Roles and access control

| Role | Created by | Can do |
|------|-----------|--------|
| student | self-register -> `pending` -> admin approves, or admin creates | own attendance, results, fees, library, notices, scan QR |
| teacher | self-register -> `pending` -> admin approves, or admin creates | only assigned subject+class: start/close sessions, view lists and reports, enter results for assigned subjects |
| admin | seed script / admin only | everything, audit logs, exports |

- Role is read from the `users` table on every request, never from a form field or JSON body.
- `role_required` guards each route; services re-check ownership (for example
  `teacher_subjects.teacher_id = current user`) so a guessed ID is not enough.
- Registration never accepts a `role=admin` value. The public form offers student/teacher only, and both start `pending`.

## 3. Attendance flow

1. Teacher picks one of their `teacher_subjects` rows, session duration, QR validity (default 120 s) and geofence (classroom preset or manual lat/lng, radius, min accuracy).
2. Server inserts `attendance_sessions` with a **snapshot** of the geofence and `session_ends_at`.
3. Server creates a random token (`secrets.token_urlsafe(32)`), stores only its SHA-256 in `attendance_qr_tokens` with `expires_at = now + qr_validity`. The QR holds only the raw token. The teacher page refreshes the token before expiry.
4. Student scans, the browser gets location, then POSTs `{token, latitude, longitude, accuracy}` with the CSRF header.
5. In **one transaction** the server: hashes the token and looks it up, checks `expires_at` against the DB clock, checks the session is active and not past `session_ends_at`, checks enrolment, checks cooldown/device, runs the Haversine check, then `INSERT`s the record. The unique key `(session_id, student_id)` is the final duplicate guard, and a duplicate-key error is turned into a friendly "already marked".
6. Every rejection writes an `attendance_security_events` row. Success and session open/close write `audit_logs`.
7. Raw student coordinates are not stored, only distance and accuracy.

## 4. Anti-proxy mechanisms

**A. QR expiry** is enforced in SQL against `expires_at`, so nothing the browser shows or sends can extend it.

**B. Device cooldown (180 s, configurable)**
- Browser creates a random ID in `localStorage` and sends it; server stores `SHA-256(id)` in `device_registrations`.
- On logout, or when a second account logs in on a device that is still signed in, the server sets `cooldown_until = now + 180 s` and `last_user_id`.
- Login from that device by a **different** user before `cooldown_until` is rejected with the seconds remaining. The same user may log back in.
- Every step is written to `device_events`.
- Limitation: the ID lives in the browser. Clearing site data, a private window, or another browser gets a new ID. This is a deterrent, not physical device identity, and no fingerprinting is claimed.

**C. Geofencing**
- Haversine distance between the student's reported point and the session's stored point.
- Accept only if accuracy <= `geo_min_accuracy_m` and distance <= `geo_radius_m`.
- Handled cases: permission denied, position unavailable, timeout, low accuracy, outside radius, missing coordinates.
- Limitation: browser location can be spoofed by developer tools or mock-location apps. It reduces casual proxy attendance but cannot prevent a determined one.

## 5. Security checklist and where it lives

| Control | Where |
|---------|-------|
| Password hashing (Werkzeug) | `auth_service` |
| CSRF (Flask-WTF) on forms and fetch via header | `extensions.py`, `base.html` meta tag |
| Secure session cookies (HttpOnly, SameSite=Lax, Secure when HTTPS) | `config.py` |
| Parameterised SQL only | `db.py` helpers |
| Login rate limit (DB-backed `login_attempts`) | `auth_service` |
| Attendance rate limit (per student, via `attendance_security_events`) | `attendance_service` |
| Safe uploads (PDF only, size cap, magic-bytes check, random stored name, stored outside `static/`) | `syllabus_service` |
| Audit logs | `audit_service` |
| Secrets from `.env` | `config.py` |
| No internal errors shown to users | `errors.py` |

## 6. Database overview (30 tables)

- **Structure:** departments, academic_years, semesters, classes, subjects, classrooms
- **People:** users, student_profiles, teacher_profiles
- **Links:** enrollments, teacher_subjects
- **Devices:** device_registrations, device_events
- **Attendance:** attendance_sessions, attendance_qr_tokens, attendance_records, attendance_security_events
- **Auth support:** login_attempts
- **Academics:** grading_schemes, grading_scheme_rules, academic_results, syllabus, learning_materials
- **Campus:** notices, fee_structures, student_fee_records, fee_transactions, library_books, library_issues
- **Audit:** audit_logs

Tables added beyond the spec's list: `attendance_qr_tokens` (token rotation), `grading_scheme_rules` (configurable grade bands), `login_attempts` (rate limiting that survives restarts).

Design decisions:
- All times are UTC and come from the server (`UTC_TIMESTAMP()`).
- Results snapshot grade, grade point and credits, so editing a scheme later does not rewrite history.
- SGPA = sum(grade_point x credits) / sum(credits) for a semester. CGPA applies the same formula across all published results. Both are computed in `academic_service`, not stored.
- Overdue library books and overdue fees are computed from dates, not stored flags.

## 7. Known constraint: camera and GPS need HTTPS

Mobile browsers only allow camera and geolocation on **HTTPS or localhost**. A phone opening `http://192.168.x.x:5000` on your Wi-Fi will not get camera or location. Phase 2's `run.py` will support a local HTTPS mode (`FLASK_HTTPS=1`, self-signed certificate via `cryptography`), and the setup guide will explain how to accept the browser warning on the phone. Testing on the same PC at `http://localhost:5000` works without it.

## 8. Phase plan

| Phase | Scope |
|-------|-------|
| 1 | Architecture, schema, project skeleton (this) |
| 2 | App factory, config, MySQL pool, init/seed scripts, auth, roles, base UI |
| 3 | QR attendance, device cooldown, geofence, audit logs |
| 4 | Student dashboard, results, SGPA/CGPA |
| 5 | Teacher dashboard and reports |
| 6 | Admin dashboard |
| 7 | Library, syllabus, notices, fees |
| 8 | Responsive polish, security review, error handling |
| 9 | Tests, setup guide, documentation |

## 9. Phase 2 amendments (what changed from the Phase 1 plan)

- `app/__init__.py` is the app factory (the plan's "init" file).
- `.env.example`: `FLASK_ENV` was renamed `APP_ENV` (Flask no longer reads `FLASK_ENV`), and
  `FLASK_HOST`, `PORT`, `SESSION_HOURS` and the login-limit settings were added.
- New files: `app/navigation.py` (role menus; links to routes that do not exist yet are shown greyed
  out with a "Soon" badge and switch on automatically), `services/profile_service.py`,
  `scripts/create_admin.py`, `pytest.ini`.
- Pulled forward from later phases because authentication needs them: `services/audit_service.py`
  (planned P3) and a minimal `services/admin_service.py` + `routes/admin.py` for approving or
  rejecting registrations (otherwise pending accounts could never become active).
- Navigation endpoint names are a contract for later phases: `attendance.scan`,
  `attendance.history`, `attendance.teacher_sessions`, `academics.results`, `academics.manage`,
  `library.my_books`, `library.manage`, `syllabus.index`, `notices.index`, `fees.my_fees`,
  `fees.manage`, `reports.index`, `admin.users`, `admin.departments`, `admin.classrooms`,
  `admin.audit_logs`.
- Schema: no changes were needed after review. Constructs that need MySQL 8.0.16+ (CHECK) and
  8.0.13+ (`DEFAULT (UTC_TIMESTAMP())`) are in use; MySQL 8.4 satisfies both. The only
  correction made in this phase was in Phase 1's file itself: `ON UPDATE UTC_TIMESTAMP()` is not
  valid MySQL, so `users.updated_at` is now set by the application.
- Login rate limiting counts failures per email (5 in 15 minutes) and per IP (20). An attacker can
  therefore temporarily lock a known email; that trade-off is accepted and will be documented in
  `SECURITY.md`. `remote_addr` is used as the client IP (no proxy headers are trusted).
- CDN assets (Bootstrap, Bootstrap Icons, Google Fonts) are loaded without Subresource Integrity
  hashes; vendoring them locally is a recommended hardening step before any real deployment.

## 10. Phase 3 amendments
* Migration `database/migrations/001_phase3_attendance.sql` (+ rollback in `migrations/rollback/`),
  applied by `scripts/migrate.py` (and by `init_db.py`), tracked in `schema_migrations`. It adds: one
  active session per assignment (generated column + unique key), three new security-event types, and a
  `(session_id, device_id)` index. Existing rows are preserved.
* New: `clock.py`, `api.py` (JSON envelope), `services/{errors,geo,qr,device,session,attendance,report}_service.py`,
  `routes/attendance.py` (pages + `/api`), `scripts/purge_old_events.py`, scanner/teacher JS, three templates.
* Changed: student login now needs the device cookie (`auth_service.authenticate(..., device_hash)`),
  logout starts the cooldown, API errors share one envelope, `security.py` touches the device at most
  once a minute for students.
* `report_service` (planned for Phase 5) now holds the attendance reports Phase 3 needs.

## 11. Phase 4 amendments
* Migration `002_phase4_student_portal.sql` (+ rollback) adds the columns listed in `docs/PHASE4_STUDENT_PORTAL.md`.
* New services: `academic_service` (results, SGPA/CGPA, calculators; Decimal only), `fee_service`, `library_service`,
  `notice_service`, `syllabus_service`, `portal_service` (dashboard), `file_service` (safe PDF serving). `profile_service`
  gained `get_context`, `get_profile_details`, `update_phone`; `auth_service` gained `change_password`.
* New routes: `academics`, `library`, `syllabus`, `notices`, `fees`, `student_api`; `students` now serves the dashboard
  and profile. A central `ServiceError` handler turns service failures into page errors or JSON envelopes.
* Navigation: students now have Academic Results, GPA Calculator, Library (`library.catalog`), Syllabus, Notices, Fees and
  Profile. The student dashboard was redesigned around real figures; the old subject table is now part of the attendance panel.
* `scripts/seed_phase4.py` adds fictional demo data; `template_filters.py` provides exact `money` / `gpa` formatting.

## 12. Phase 5 part 1 amendments (teacher side)
* Migration `003_phase5_teacher_tools.sql` (+ rollback): `attendance_corrections`, `attendance_records.source`,
  `syllabus.is_published`, `learning_materials.description`, `notices.class_id` and the `material` category. The optional
  append-only triggers are installed separately by `scripts/protect_audit_tables.py` (needs MySQL admin rights).
* New services: `teacher_service` (access scope, dashboard, reports), `correction_service`, `result_service`, `content_service`
  (syllabus and materials), `teacher_notice_service`, `upload_service` (validated PDF storage). `routes/teachers.py` now holds
  all teacher pages. `report_service` gained `rows_to_csv`; "conducted sessions" also counts corrected sessions.
* Phase 4 services adjusted: notices honour `class_id`, students see only published syllabus units.
* Teacher navigation was replaced: My Classes, Attendance Sessions, Session History, Attendance Reports, Results, Syllabus &
  Materials, Notices. Administrator pages (part 2) are still pending.
* `scripts/seed_phase5.py` adds three finished demo sessions.

## 13. Phase 6 amendments (integration audit)
* New checkers: `scripts/lint_sql.py` (SQL vs schema), `tests/test_static_checks.py` (route guards, links, assets, CSP, CSRF, XSS,
  secrets, error leakage, reviewed dynamic SQL, proxy headers). Guards now carry an `nx_access` marker so they can be audited.
* New tooling: `scripts/generate_docs.py` (API and database docs), `scripts/db_backup.py`, `wsgi.py`, `requirements-prod.txt`,
  `TRUSTED_PROXY_HOPS` (ProxyFix) for deployments behind a reverse proxy.
* Dead Phase 2 helpers and unused imports were removed.
* Still missing: the administrator panel (Phase 5 part 2). See `docs/IMPLEMENTATION_REPORT.md`.
