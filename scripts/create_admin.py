r"""Create a real administrator account (no demo data).

  python scripts\create_admin.py
"""
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app                                              # noqa: E402
from app.config import Config                                           # noqa: E402
from app.db import execute, query_one                                   # noqa: E402
from app.services import audit_service                                  # noqa: E402
from app.services.auth_service import (EMAIL_RE, hash_password,         # noqa: E402
                                       normalize_email, validate_password)


def main():
    app = create_app(Config)
    with app.app_context():
        name = " ".join(input("Full name: ").split())
        email = normalize_email(input("Email: "))
        if len(name) < 2 or not EMAIL_RE.match(email):
            sys.exit("Please enter a valid name and email.")
        if query_one("SELECT id FROM users WHERE email = %s", (email,)):
            sys.exit("An account with that email already exists.")
        password = getpass.getpass("Password (typing is hidden): ")
        if password != getpass.getpass("Repeat password: "):
            sys.exit("Passwords do not match.")
        problem = validate_password(password, email)
        if problem:
            sys.exit(problem)
        uid = execute("INSERT INTO users (email, password_hash, full_name, role, status) "
                      "VALUES (%s, %s, %s, 'admin', 'active')",
                      (email, hash_password(password), name)).lastrowid
        audit_service.log("admin.create_cli", user_id=uid, entity_type="user", entity_id=uid)
        print(f"Administrator {email} created.")


if __name__ == "__main__":
    main()
