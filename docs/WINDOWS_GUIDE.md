# NEXORA on Windows 11: install, run, test, back up

All commands are for **Command Prompt** (not PowerShell). `path\to\nexora` is the folder that contains `run.py`.

## 1. One-time installation
1. Install **Python 3.11 or 3.12** from python.org; tick *Add python.exe to PATH*. Check: `python --version`
2. Install **MySQL Community Server 8.x** (with the MySQL Installer). Remember the root password. Check the service:
   `sc query MySQL80`   (start it with `net start MySQL80` in an *Administrator* Command Prompt; the service name can differ)
3. Optional: install VS Code.

## 2. Set up the project
```
cd path\to\nexora
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
notepad .env
```
In `.env` paste the printed value after `SECRET_KEY=` and set `DB_PASSWORD=` to a new password for the application account.
`.env` is never committed (it is in `.gitignore`); `copy` does **not** overwrite an existing `.env`, so if you already have one,
keep it and only add new lines from `.env.example`.

## 3. Create the database
```
python scripts\init_db.py --bootstrap
python scripts\init_db.py
```
`--bootstrap` asks for the MySQL **root** password once and creates `nexora`, `nexora_test` and the `nexora_app` user.
The second command creates the tables and applies every migration. Upgrading an existing installation: `python scripts\migrate.py`.
(Never drop the database as a routine fix. Back up first, see section 7.)

## 4. Demo data and accounts
```
python scripts\seed_demo.py
python scripts\seed_phase4.py
python scripts\seed_phase5.py
```
Each script says so and changes nothing if its data already exists. Everything is fictional. Password for all demo accounts:
`Nexora@2026` (development only).

| Role | Sign in with |
|---|---|
| Administrator | admin@nexora.demo |
| Teacher | asha.verma@nexora.demo, rohan.iyer@nexora.demo |
| Student | aarav.sharma@nexora.demo, diya.patel@nexora.demo, kabir.singh@nexora.demo, meera.nair@nexora.demo, ishaan.gupta@nexora.demo |
| Waiting for approval | zoya.khan@nexora.demo (approve her as the administrator) |

A real administrator without demo data: `python scripts\create_admin.py`.

## 5. Run
```
python run.py
```
Open http://localhost:5000 (health check: http://localhost:5000/health shows whether MySQL is reachable).

**Phone camera and location need HTTPS:**
```
set FLASK_HTTPS=1
set FLASK_HOST=0.0.0.0
python run.py
ipconfig
```
On the phone (same Wi-Fi) open `https://<your IPv4 address>:5000` and accept the self-signed certificate warning. Allow Python through
the Windows firewall for private networks. Debug mode is switched off automatically when the server is not on localhost.

## 6. Tests
```
python scripts\lint_sql.py                      checks every SQL statement against the schema (no database needed)
python scripts\init_db.py --test                prepares the TEST database
python -m pytest tests -v                       everything; MySQL tests are skipped automatically if MySQL is unreachable
python -m pytest tests\test_static_checks.py tests\test_phase2_unit.py tests\test_phase3_unit.py tests\test_phase4_unit.py tests\test_phase5_unit.py -v
python -m pytest tests -v -k integration        only the MySQL integration tests
```
Tests only ever touch `nexora_test`; the configuration refuses to run them against your real database.
Expected: every test passes. Please send me the full text of any failure. Optional extra test: `python scripts\protect_audit_tables.py --test`
enables the append-only trigger test.

## 7. Backup and restore
```
python scripts\db_backup.py backup               writes backups\nexora_YYYYMMDD_HHMMSS.sql
python scripts\db_backup.py restore backups\FILE.sql
```
Back up before running `migrate.py` on real data. Restore replaces the tables in the file and asks you to type the database name first.
If `mysqldump` is not found: `set PATH=%PATH%;C:\Program Files\MySQL\MySQL Server 8.4\bin`

## 8. Production-style start (not the development server)
```
python -m pip install -r requirements-prod.txt
set APP_ENV=production
waitress-serve --listen=127.0.0.1:8000 wsgi:app
```
Put an HTTPS reverse proxy in front, set `TRUSTED_PROXY_HOPS=1`, and use a strong `SECRET_KEY`. Production mode makes cookies Secure and
sends HSTS. Running locally with waitress is **not** a deployment.

## 9. Common problems
| Message | Fix |
|---|---|
| `SECRET_KEY is missing...` | Fill `SECRET_KEY` in `.env` |
| `MySQL rejected DB_USER / DB_PASSWORD` or `Database ... does not exist` | `python scripts\init_db.py --bootstrap` and make `.env` match |
| `Could not connect to MySQL` | Start the service (`net start MySQL80`) |
| Migration says a column already exists | Send me the message; do not drop the database. Restore your backup if needed |
| Phone: camera or location blocked | Use the HTTPS address, accept the warning, then allow both permissions |
| Student login: "device cookie" message | Reload the login page (it issues the cookie) and sign in again |
| Student login: "try again in N seconds" | The 180 s device cooldown after another student used that browser |
