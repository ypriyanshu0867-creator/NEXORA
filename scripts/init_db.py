r"""Create the NEXORA database objects.

Run from the project folder with the virtual environment active:

  python scripts\init_db.py --bootstrap     First time only: creates the two
                                            databases and the app user. Asks for
                                            your MySQL admin (root) password.
  python scripts\init_db.py                 Creates the 30 tables in DB_NAME.
  python scripts\init_db.py --test          Creates them in TEST_DB_NAME.
  python scripts\init_db.py --reset --test --yes
                                            Drops and recreates the TEST tables.
  python scripts\init_db.py --reset         Drops and recreates the main tables
                                            (asks you to type the database name).
"""
import argparse
import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mysql.connector                      # noqa: E402
from mysql.connector import errors          # noqa: E402

from app.config import Config               # noqa: E402

SCHEMA_FILE = ROOT / "database" / "schema.sql"
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def split_statements(sql_text):
    """Remove -- comments, then split on semicolons (the schema has no
    semicolons inside strings)."""
    without_comments = re.sub(r"--[^\n]*", "", sql_text)
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def connect(user, password, database=None):
    kwargs = dict(host=Config.DB_HOST, port=Config.DB_PORT, user=user, password=password,
                  charset="utf8mb4", autocommit=True, connection_timeout=10)
    if database:
        kwargs["database"] = database
    return mysql.connector.connect(**kwargs)


def _text(value):
    return value.decode() if isinstance(value, (bytes, bytearray)) else value


def bootstrap():
    names = (Config.MAIN_DB_NAME, Config.TEST_DB_NAME)
    for name in names:
        if not NAME_RE.match(name):
            sys.exit(f"Database name {name!r} may only contain letters, numbers and underscores.")
    if not Config.DB_USER or not Config.DB_PASSWORD:
        sys.exit("Set DB_USER and DB_PASSWORD in your .env file first. These are the credentials "
                 "of the NEW application account that will be created (not your root login).")
    if Config.DB_USER.lower() == "root":
        sys.exit("Use a dedicated application user (for example nexora_app), not root.")

    admin_user = input("MySQL admin user [root]: ").strip() or "root"
    admin_password = getpass.getpass("MySQL admin password (typing is hidden): ")
    try:
        conn = connect(admin_user, admin_password)
    except errors.Error as exc:
        sys.exit(f"Could not connect to MySQL as {admin_user}: {exc}\n"
                 "Check that the MySQL service is running and the password is right.")
    cur = conn.cursor()
    hosts = ["localhost", "127.0.0.1"] if Config.DB_HOST in ("127.0.0.1", "localhost") else ["%"]
    for name in names:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
    for host in hosts:
        cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s",
                    (Config.DB_USER, host, Config.DB_PASSWORD))
        for name in names:
            cur.execute(f"GRANT ALL PRIVILEGES ON `{name}`.* TO %s@%s", (Config.DB_USER, host))
    conn.close()
    print(f"OK: databases '{names[0]}' and '{names[1]}' exist, and user '{Config.DB_USER}' "
          "has access to both.")
    print("If the user already existed, its password was NOT changed.")


def init_schema(db_name, reset, assume_yes):
    if not NAME_RE.match(db_name):
        sys.exit(f"Invalid database name {db_name!r}.")
    try:
        conn = connect(Config.DB_USER, Config.DB_PASSWORD, db_name)
    except errors.Error as exc:
        if exc.errno == 1049:
            sys.exit(f"Database '{db_name}' does not exist. Run first:\n"
                     "  python scripts\\init_db.py --bootstrap")
        if exc.errno == 1045:
            sys.exit("MySQL rejected DB_USER / DB_PASSWORD from your .env file "
                     "(or the user does not exist yet). Run:  python scripts\\init_db.py --bootstrap")
        sys.exit(f"Could not connect to MySQL: {exc}")
    cur = conn.cursor()

    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE'", (db_name,))
    existing = [_text(row[0]) for row in cur.fetchall()]

    if existing and not reset:
        print(f"Database '{db_name}' already has {len(existing)} table(s); base tables unchanged.\n"
              "Use --reset to drop and recreate them (this deletes all data in that database).")
        from migrate import apply_pending
        applied = apply_pending(conn)
        print(f"Applied migrations: {', '.join(applied)}" if applied else "Migrations: already up to date.")
        return
    if reset and existing:
        is_test = "test" in db_name.lower()
        if not (assume_yes and is_test):
            typed = input(f"This will DELETE ALL DATA in '{db_name}'. Type the database name to confirm: ")
            if typed.strip() != db_name:
                sys.exit("Cancelled. Nothing was changed.")
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for name in existing:
            cur.execute(f"DROP TABLE IF EXISTS `{name}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")

    statements = split_statements(SCHEMA_FILE.read_text(encoding="utf8"))
    expected = sum(1 for s in statements if s.upper().startswith("CREATE TABLE"))
    for stmt in statements:
        try:
            cur.execute(stmt)
        except errors.Error as exc:
            sys.exit(f"Schema failed on:\n  {stmt[:90]}...\nMySQL said: {exc}\n"
                     "Fix the problem, then re-run with --reset.")

    cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE'", (db_name,))
    created = cur.fetchone()[0]
    if created != expected:
        sys.exit(f"Expected {expected} tables but found {created}.")
    print(f"OK: created {created} tables in '{db_name}'.")
    from migrate import apply_pending
    applied = apply_pending(conn)
    conn.close()
    print(f"Applied migrations: {', '.join(applied)}" if applied else "Migrations: none pending.")


def main():
    parser = argparse.ArgumentParser(description="Initialise the NEXORA database.")
    parser.add_argument("--bootstrap", action="store_true", help="create databases and the app user")
    parser.add_argument("--test", action="store_true", help="target the test database")
    parser.add_argument("--reset", action="store_true", help="drop and recreate tables")
    parser.add_argument("--yes", action="store_true", help="skip confirmation (test databases only)")
    args = parser.parse_args()

    if args.bootstrap:
        bootstrap()
        return
    init_schema(Config.TEST_DB_NAME if args.test else Config.DB_NAME, args.reset, args.yes)


if __name__ == "__main__":
    main()
