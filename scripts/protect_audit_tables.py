r"""Install (or remove) append-only triggers on audit_logs and attendance_corrections.

  python scripts\protect_audit_tables.py            main database
  python scripts\protect_audit_tables.py --test     test database
  python scripts\protect_audit_tables.py --remove   remove the triggers again

WHY A SEPARATE SCRIPT: with MySQL's default binary logging, creating triggers needs the SUPER
(or SET_USER_ID) privilege, which the application account (nexora_app) deliberately lacks. Run this
ONCE with your MySQL admin login; it asks for the password and never stores it. The application works
without the triggers, but then nothing in the database stops an UPDATE or DELETE of these rows.

WHAT IT PROTECTS AGAINST: ordinary application statements (and bugs) cannot change or delete audit
rows. WHAT IT DOES NOT: a database administrator can drop the triggers or edit rows directly, and
anyone with file or backup access can alter data. For stronger guarantees, copy logs to write-once
storage or an external logging service.
"""
import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mysql.connector import errors                      # noqa: E402

from init_db import Config, connect                      # noqa: E402

TRIGGERS = [("trg_audit_logs_no_update", "BEFORE UPDATE", "audit_logs"),
            ("trg_audit_logs_no_delete", "BEFORE DELETE", "audit_logs"),
            ("trg_corrections_no_update", "BEFORE UPDATE", "attendance_corrections"),
            ("trg_corrections_no_delete", "BEFORE DELETE", "attendance_corrections")]


def main():
    parser = argparse.ArgumentParser(description="Append-only protection for audit tables.")
    parser.add_argument("--test", action="store_true", help="target the test database")
    parser.add_argument("--remove", action="store_true", help="remove the triggers")
    args = parser.parse_args()
    db_name = Config.TEST_DB_NAME if args.test else Config.DB_NAME
    admin = input("MySQL admin user [root]: ").strip() or "root"
    password = getpass.getpass("MySQL admin password (typing is hidden): ")
    try:
        conn = connect(admin, password, db_name)
        cur = conn.cursor()
        for name, timing, table in TRIGGERS:
            cur.execute(f"DROP TRIGGER IF EXISTS `{name}`")
            if not args.remove:
                cur.execute(f"CREATE TRIGGER `{name}` {timing} ON `{table}` FOR EACH ROW "
                            f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} is append-only'")
        conn.close()
    except errors.Error as exc:
        sys.exit(f"Could not change triggers in '{db_name}': {exc}\n"
                 "Make sure migrations are applied first (python scripts\\migrate.py) and the login is an admin.")
    print(f"Append-only triggers {'removed from' if args.remove else 'installed on'} '{db_name}'.")


if __name__ == "__main__":
    main()
