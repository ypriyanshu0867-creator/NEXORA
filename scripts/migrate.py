r"""Apply pending database migrations (database\migrations\NNN_name.sql).

  python scripts\migrate.py            main database (DB_NAME)
  python scripts\migrate.py --test     test database (TEST_DB_NAME)

Applied versions are recorded in the schema_migrations table, so running it
again is safe. Existing data is never dropped. init_db.py also calls this.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mysql.connector import errors          # noqa: E402

from init_db import Config, connect, split_statements   # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"


def apply_pending(conn):
    """Run every migration not yet recorded. Returns the list applied."""
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version VARCHAR(100) NOT NULL PRIMARY KEY, "
                "applied_at DATETIME NOT NULL DEFAULT (UTC_TIMESTAMP())"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
    cur.execute("SELECT version FROM schema_migrations")
    done = {row[0].decode() if isinstance(row[0], (bytes, bytearray)) else row[0]
            for row in cur.fetchall()}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        if path.stem in done:
            continue
        for stmt in split_statements(path.read_text(encoding="utf8")):
            try:
                cur.execute(stmt)
            except errors.Error as exc:
                sys.exit(f"Migration {path.name} failed on:\n  {stmt[:100]}...\nMySQL said: {exc}\n"
                         "Nothing after this statement was applied and the migration is NOT "
                         "recorded as done. Fix the cause and run this command again. Statements "
                         "that already succeeded may need undoing first: see "
                         "database\\migrations\\rollback\\.")
        cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
        applied.append(path.stem)
    return applied


def main():
    parser = argparse.ArgumentParser(description="Apply NEXORA database migrations.")
    parser.add_argument("--test", action="store_true", help="target the test database")
    args = parser.parse_args()
    db_name = Config.TEST_DB_NAME if args.test else Config.DB_NAME
    try:
        conn = connect(Config.DB_USER, Config.DB_PASSWORD, db_name)
    except errors.Error as exc:
        sys.exit(f"Could not connect to '{db_name}': {exc}")
    applied = apply_pending(conn)
    conn.close()
    print(f"Applied: {', '.join(applied)}" if applied else f"'{db_name}' is already up to date.")


if __name__ == "__main__":
    main()
