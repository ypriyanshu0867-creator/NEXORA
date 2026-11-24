# NEXORA project-wide implementation report (Phase 6 inspection)

Honest status: **Nexora is NOT complete.** The administrator panel (Phase 5 Steps 9-15) has not been built, and nothing has been
run against a real MySQL server or a real browser. Everything below separates what was *verified here* from what *must be verified
on your Windows machine*.

## 1. What exists
* 14 route modules, **72 routes** (5 public; every other route is guarded, audited by a test), ~4,400 lines of application Python,
  25 service modules, 35 templates, 4 JavaScript files, 3 migrations on a 32-table MySQL schema.
* Student: dashboard, QR scanner, attendance history, results, SGPA/CGPA calculators, library, syllabus + material downloads, notices,
  fees (read-only tracking), profile and password change.
* Teacher: dashboard, assigned classes, QR session control, session history, audited attendance corrections, attendance reports (CSV),
  result entry and publishing, syllabus and PDF materials, class notices.
* Admin: dashboard counts and approval/rejection of self-registered accounts **only**.
* Security layers: password hashing, DB-backed role checks, CSRF, CSP and security headers, rate limits, device cooldown, geofence,
  safe file serving, parameterised SQL, audit log.

## 2. What is missing (the blocker)
Admin menu items with no page behind them: **Users, Departments & Classes, Classrooms, Library management, Fees management, Audit
Logs.** Consequences: no screen to assign teachers, enrol students, edit departments/classes/subjects/semesters, configure grading
schemes or classrooms, issue/return books, record payments, publish institution notices, unpublish a result, or read the audit log.
Today those facts come from the seed scripts or SQL. Final-acceptance criteria 17-20 (admin dashboard, user management, academic
management, audit logs) and the admin parts of 2, 8 and 11 therefore cannot be met until Phase 5 part 2 is built.

## 3. Checks run in this environment (no MySQL, no browser)
| Check | Result |
|---|---|
| Python syntax (warnings as errors) | Passed |
| 104 unit/structural tests via stub modules for Flask-WTF, mysql-connector, pytest: 103 executed and passed, 1 not executed (needs real Flask-WTF) | Passed |
| `scripts\lint_sql.py`: every SQL string vs the real schema + migrations (tables, columns, placeholder counts, INSERT arity, FK targets, CHECK-vs-FK-action rule) | Passed, 0 problems |
| Mutation tests proving the linter and guard audit catch mistakes | Passed |
| Route-guard audit (all routes guarded or allow-listed; roles match URL space; GET-only routes never call write services; anonymous GETs never reach a view) | Passed |
| Mock-based service-flow, page and API checks for Phases 3, 4 and 5 (16 + 13 + 49 + 48 = 126 checks; not committed) | Passed |
| MySQL integration tests (91 written) | **NOT RUN** |
| Real CSRF enforcement; JavaScript, camera, GPS and layout in a browser | **NOT RUN** |

## 4. Findings and what was done
| # | Finding | Action |
|---|---|---|
| 1 | Admin panel not built (see section 2) | Open. Next work item |
| 2 | No unguarded routes, no missing CSRF tokens, no inline scripts/styles, no `innerHTML`/`|safe`, no secrets in code | Pinned as permanent tests |
| 3 | 13 SQL statements are assembled dynamically; all reviewed: variable parts are fixed fragments, a fixed column tuple or runs of `%s` | Allow-list test fails if a new one appears unreviewed |
| 4 | Dead code from Phase 2 (5 helper functions) and 9 unused imports | Removed |
| 5 | Behind a reverse proxy every client would share one IP, collapsing login rate limits and audit IPs | Added `TRUSTED_PROXY_HOPS` (ProxyFix) + test |
| 6 | No production entry point | Added `wsgi.py`, `requirements-prod.txt` (waitress) |
| 7 | Triggers need SUPER on default MySQL 8 | (Phase 5) moved to the optional admin-run `protect_audit_tables.py` |
| 8 | No backup/restore tooling | Added `scripts\db_backup.py` (mysqldump/mysql) |
| 9 | API and DB docs would go stale | Generated from live routes/schema by `scripts\generate_docs.py` |
| 10 | Pagination arithmetic is repeated in three services | Left as is (low risk); noted for refactor |
| 11 | Ad-hoc Phase 2 smoke scripts assert the old dashboards | Obsolete, not part of the repo |

No functional defect was found by static analysis. That does **not** mean none exists: the highest-risk areas are exactly the ones that
only MySQL/browser runs can expose (SQL semantics, locking and concurrency, `ONLY_FULL_GROUP_BY`, camera/GPS permissions, CDN loading).

## 5. Risks that only your machine can confirm
1. `scripts\init_db.py` and the three migrations on MySQL 8.4 (generated column + unique key, ENUM changes, CHECK constraints).
2. Concurrency: simultaneous scans, simultaneous logins on one device (row locks).
3. The phone scanner (HTTPS, self-signed certificate, camera and location prompts, html5-qrcode from a CDN).
4. Template layout at phone/tablet widths.
5. Real CSRF enforcement (Flask-WTF) and `Retry-After` handling.

## 6. Final acceptance criteria (30) at this moment
Legend: **V** = verified by checks actually run here (static/mock level only), **W** = written, needs your MySQL/browser run, **X** = not built.

| # | Criterion | Status |
|---|---|---|
| 1-2 | Three roles; authentication | W (role guards: V) |
| 3-6 | QR attendance, QR expiry, device cooldown, geofencing | W (logic, validation order and cooldown policy: V via unit tests) |
| 7 | Attendance reports accurate | W |
| 8-11 | Student dashboard, results, SGPA, CGPA | W (formulas and rounding: V via unit tests) |
| 12-15 | Library, syllabus, notices, fees (student side) | W |
| 16 | Teacher dashboard | W |
| 17 | Admin dashboard | W (counts + approvals only) |
| 18-19 | User management; academic management | **X** |
| 20 | Audit logs (written yes, viewer **X**) | partly X |
| 21 | Backend authorization enforced | V (structure), W (data isolation needs MySQL) |
| 22 | Responsive pages | W (not viewed in a browser) |
| 23 | Database relationships consistent | V (static schema/FK checks), W (real data) |
| 24 | File access secure | V (path-traversal logic), W (real files) |
| 25 | Automated tests provided and executed | Provided; executed only at the unit/mock level |
| 26 | Regression checks | V at unit/mock level |
| 27 | Documentation complete | Partly: API, database, phase and Windows guides done; project report, user guide, demo script, viva, slides **deferred** until the app is complete |
| 28 | Windows setup instructions | Done (`docs\WINDOWS_GUIDE.md`), untested on Windows |
| 29 | Demo accounts and data | Done (seed scripts) |
| 30 | Known limitations documented | Done (this report and the phase documents) |

## 7. Plan to finish
1. **Phase 5 part 2:** admin users and assignments, enrolments, departments/years/semesters/classes/subjects, classrooms, grading
   schemes (with history protection), library issue/return, fee structures and payments, institution notices, audit viewer,
   administrative reports and CSV exports, with tests.
2. Re-run all static checks; extend the integration tests.
3. Finish documentation: PROJECT_REPORT, USER_GUIDE, PROJECT_DEMO, VIVA_QUESTIONS, PRESENTATION_OUTLINE.
4. You run the MySQL tests on Windows and send me failures; fix and re-run.
