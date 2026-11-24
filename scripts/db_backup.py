r"""Back up or restore the NEXORA database with MySQL's own tools.

  python scripts\db_backup.py backup                  writes backups\nexora_YYYYMMDD_HHMMSS.sql
  python scripts\db_backup.py backup --test           backs up the test database instead
  python scripts\db_backup.py restore backups\FILE.sql   asks you to type the database name first

Run a backup BEFORE applying migrations on a database that holds real data. A restore REPLACES the tables
that are in the file. The password is read from .env and handed to MySQL through an environment variable
(never on the command line). Needs mysqldump / mysql (installed with MySQL Server); the usual Windows
install folders are searched if they are not on PATH.
"""
import argparse
import datetime
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402


def find_tool(name):
    found = shutil.which(name)
    if found:
        return found
    for pattern in (rf"C:\Program Files\MySQL\MySQL Server *\bin\{name}.exe", rf"C:\Program Files (x86)\MySQL\MySQL Server *\bin\{name}.exe"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    sys.exit(f"Could not find {name}. Add MySQL's bin folder to PATH, for example:\n"
             r'  set PATH=%PATH%;C:\Program Files\MySQL\MySQL Server 8.4\bin')


def base_command(tool, db_name):
    return [tool, f"--host={Config.DB_HOST}", f"--port={Config.DB_PORT}", f"--user={Config.DB_USER}",
            "--default-character-set=utf8mb4", db_name]


def main():
    parser = argparse.ArgumentParser(description="Back up or restore the NEXORA database.")
    sub = parser.add_subparsers(dest="action", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--test", action="store_true")
    b.add_argument("--out")
    r = sub.add_parser("restore")
    r.add_argument("file")
    r.add_argument("--test", action="store_true")
    args = parser.parse_args()
    db_name = Config.TEST_DB_NAME if args.test else Config.DB_NAME
    env = dict(os.environ, MYSQL_PWD=Config.DB_PASSWORD)

    if args.action == "backup":
        out = Path(args.out) if args.out else ROOT / "backups" / f"{db_name}_{datetime.datetime.now():%Y%m%d_%H%M%S}.sql"
        out.parent.mkdir(parents=True, exist_ok=True)
        command = base_command(find_tool("mysqldump"), db_name)
        command[1:1] = ["--no-tablespaces", "--single-transaction", "--routines", "--triggers", f"--result-file={out}"]
        if subprocess.run(command, env=env).returncode != 0:
            sys.exit("Backup failed (see the message above). Nothing was changed in the database.")
        print(f"Backup written to {out}")
        return

    path = Path(args.file)
    if not path.is_file():
        sys.exit(f"File not found: {path}")
    if input(f"This REPLACES tables in '{db_name}' with the contents of {path.name}. Type the database name to continue: ").strip() != db_name:
        sys.exit("Cancelled. Nothing was changed.")
    with open(path, "rb") as handle:
        if subprocess.run(base_command(find_tool("mysql"), db_name), env=env, stdin=handle).returncode != 0:
            sys.exit("Restore failed (see the message above).")
    print(f"Restored '{db_name}' from {path}")


if __name__ == "__main__":
    main()
