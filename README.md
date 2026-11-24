# NEXORA - Smart Attendance System Using QR Code with Anti-Proxy Verification

Status: **not complete.** Student portal, teacher tools and the QR attendance system are written; the **administrator panel is not built yet**
(only the dashboard and account approvals exist). Nothing has been run against a real MySQL server or browser. Read
`docs/IMPLEMENTATION_REPORT.md` for the exact state, `docs/WINDOWS_GUIDE.md` for installation and tests, and the per-phase documents
(`docs/PHASE3_ATTENDANCE.md`, `PHASE4_STUDENT_PORTAL.md`, `PHASE5_TEACHER_TOOLS.md`). `docs/API_DOCUMENTATION.md` and
`docs/DATABASE_DOCUMENTATION.md` are generated from the code by `python scripts\generate_docs.py`.

## Quick start (Windows 11, Command Prompt, MySQL 8.x already installed and running)

Open Command Prompt in the project folder (the one containing `run.py`):

```
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
notepad .env
```

In `.env`, paste the printed value as `SECRET_KEY=` and choose a `DB_PASSWORD`
(this is the password of the new `nexora_app` MySQL account, not your root password). Save and close.

```
python scripts\init_db.py --bootstrap
python scripts\init_db.py
python scripts\seed_demo.py
python run.py
```

Already set up earlier? Just run `python scripts\migrate.py` once to add the Phase 3 and 4 database changes, then
`python scripts\seed_phase4.py` (Phase 4 demo data) and `python scripts\seed_phase5.py` (demo attendance history), both after `seed_demo.py`.

`--bootstrap` asks for your MySQL root password once, creates the `nexora` and `nexora_test`
databases and the `nexora_app` user. The next command creates the 30 tables. `seed_demo.py` adds
fictional demo accounts. Then open **http://localhost:5000**.

Demo accounts (password for all: `Nexora@2026`):

| Role | Email |
|------|-------|
| Admin | admin@nexora.demo |
| Teacher | asha.verma@nexora.demo, rohan.iyer@nexora.demo |
| Student | aarav.sharma@nexora.demo (and four more) |
| Pending student | zoya.khan@nexora.demo (log in as admin and approve her first) |

To create a real administrator instead of using demo data: `python scripts\create_admin.py`.

## Checks

```
python -m pytest tests -v                (unit tests run anywhere; integration tests need MySQL)
python scripts\init_db.py --test         (run once before the integration tests)
```

Health check (proves the app can reach MySQL): http://localhost:5000/health

## Common errors

| Message | Fix |
|---------|-----|
| `SECRET_KEY is missing...` | Fill `SECRET_KEY` in `.env` (command above). |
| `MySQL rejected DB_USER / DB_PASSWORD` | Run `python scripts\init_db.py --bootstrap`, and make sure `.env` values match. |
| `Could not connect to MySQL` | Start the service: `net start MySQL80` in an Administrator Command Prompt (service name may differ). |
| `Database 'nexora' does not exist` | Run `python scripts\init_db.py --bootstrap`. |
| `'python' is not recognized` | Reinstall Python and tick "Add python.exe to PATH". |

The full beginner guide (installing Python, MySQL and VS Code) is written in Phase 9.
