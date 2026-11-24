import re
import secrets
import time
"""Registration, password hashing and login with rate limiting.

Business rules live here; the routes only translate HTTP to these calls.
"""
import re
import secrets
from dataclasses import dataclass
from functools import lru_cache

from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from app.db import DatabaseError, IntegrityError, execute, is_duplicate_key, query_one, transaction
from app.services import audit_service, device_service

# PBKDF2-HMAC-SHA256 with 600,000 iterations (OWASP guidance) works on every
# Python build, unlike scrypt which needs a specific OpenSSL.
PASSWORD_HASH_METHOD = "pbkdf2:sha256:600000"

# The ONLY roles a visitor may request. Admin accounts are never created by
# the public form; the role is also re-validated server-side below.
SELF_REGISTER_ROLES = ("student", "teacher")

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_/. ]{2,29}$")


class RegistrationError(Exception):
    def __init__(self, errors):
        super().__init__("registration failed")
        self.errors = errors


@dataclass
class LoginResult:
    ok: bool
    user: dict = None
    code: str = ""
    message: str = ""
    retry_after: int = 0


# ----------------------------------------------------------------------
# Passwords
# ----------------------------------------------------------------------
def hash_password(password):
    return generate_password_hash(password, method=PASSWORD_HASH_METHOD)


@lru_cache(maxsize=1)
def _dummy_hash():
    """Hash checked when the email is unknown, so timing does not reveal it."""
    return hash_password(secrets.token_hex(16))


def validate_password(password, email=""):
    """Return an error message, or None if the password is acceptable."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password) > 128:
        return "Password must be at most 128 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain at least one letter and one number."
    if email and password.lower() == email.lower():
        return "Password must not be the same as your email."
    return None


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------
def normalize_email(value):
    return (value or "").strip().lower()


def validate_registration(data, valid_department_ids):
    """Validate form data. Returns (cleaned_values, errors_by_field)."""
    errors = {}
    full_name = " ".join((data.get("full_name") or "").split())
    email = normalize_email(data.get("email"))
    password = data.get("password") or ""
    confirm = data.get("confirm_password") or ""
    role = (data.get("role") or "").strip()
    identifier = (data.get("identifier") or "").strip()

    if not (2 <= len(full_name) <= 120) or not full_name.isprintable():
        errors["full_name"] = "Enter your full name (2 to 120 characters)."
    if len(email) > 190 or not EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address."
    if role not in SELF_REGISTER_ROLES:
        errors["role"] = "Choose Student or Teacher."
    try:
        department_id = int(data.get("department_id") or 0)
    except (TypeError, ValueError):
        department_id = 0
    if department_id not in valid_department_ids:
        errors["department_id"] = "Choose your department."
    if not IDENTIFIER_RE.match(identifier):
        label = "employee code" if role == "teacher" else "roll number"
        errors["identifier"] = f"Enter a valid {label} (3 to 30 letters, numbers, - _ / .)."
    pw_error = validate_password(password, email)
    if pw_error:
        errors["password"] = pw_error
    elif password != confirm:
        errors["confirm_password"] = "Passwords do not match."

    cleaned = {
        "full_name": full_name, "email": email, "password": password,
        "role": role, "department_id": department_id, "identifier": identifier,
    }
    return cleaned, errors


def register_user(cleaned, ip):
    """Create a PENDING account. An administrator must approve it before login.

    The role comes from the validated whitelist, and status is always
    'pending' regardless of anything the browser sent.
    """
    if cleaned["role"] not in SELF_REGISTER_ROLES:
        raise RegistrationError({"role": "Choose Student or Teacher."})

    recent = query_one(
        "SELECT COUNT(*) AS c FROM audit_logs WHERE action = 'user.register' "
        "AND ip_address = %s AND created_at > UTC_TIMESTAMP() - INTERVAL 1 HOUR", (ip,))
    if recent and recent["c"] >= current_app.config["REGISTER_MAX_PER_IP_PER_HOUR"]:
        raise RegistrationError({"_form": "Too many registrations from this network. Try again later."})

    try:
        with transaction():
            user_id = execute(
                "INSERT INTO users (email, password_hash, full_name, role, status) "
                "VALUES (%s, %s, %s, %s, 'pending')",
                (cleaned["email"], hash_password(cleaned["password"]),
                 cleaned["full_name"], cleaned["role"])).lastrowid
            if cleaned["role"] == "student":
                execute("INSERT INTO student_profiles (user_id, roll_number, department_id) "
                        "VALUES (%s, %s, %s)",
                        (user_id, cleaned["identifier"], cleaned["department_id"]))
            else:
                execute("INSERT INTO teacher_profiles (user_id, employee_code, department_id) "
                        "VALUES (%s, %s, %s)",
                        (user_id, cleaned["identifier"], cleaned["department_id"]))
            audit_service.log("user.register", user_id=user_id, entity_type="user",
                              entity_id=user_id, details={"role": cleaned["role"]}, ip=ip)
    except IntegrityError as exc:
        if is_duplicate_key(exc, "uq_users_email"):
            raise RegistrationError({"email": "This email is already registered."}) from None
        if is_duplicate_key(exc, "uq_student_roll"):
            raise RegistrationError({"identifier": "This roll number is already registered."}) from None
        if is_duplicate_key(exc, "uq_teacher_employee"):
            raise RegistrationError({"identifier": "This employee code is already registered."}) from None
        raise
    return user_id


# ----------------------------------------------------------------------
# Login
# ----------------------------------------------------------------------
def _rate_limit_wait(email, ip):
    """Seconds the caller must wait, or 0 if the attempt is allowed."""
    cfg = current_app.config
    window = cfg["LOGIN_WINDOW_MINUTES"] * 60
    wait = 0
    for column, value, limit in (("email", email, cfg["LOGIN_MAX_FAILURES_PER_EMAIL"]),
                                 ("ip_address", ip, cfg["LOGIN_MAX_FAILURES_PER_IP"])):
        row = query_one(
            f"SELECT COUNT(*) AS c, "
            f"TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), MIN(attempted_at) + INTERVAL %s SECOND) AS wait "
            f"FROM login_attempts WHERE {column} = %s AND succeeded = 0 "
            f"AND attempted_at > UTC_TIMESTAMP() - INTERVAL %s SECOND",
            (window, value, window))
        if row and row["c"] >= limit:
            wait = max(wait, int(row["wait"] or 1))
    return wait


def _record_attempt(email, ip, succeeded):
    execute("INSERT INTO login_attempts (email, ip_address, succeeded) VALUES (%s, %s, %s)",
            (email[:190], ip or "unknown", 1 if succeeded else 0))


_STATUS_MESSAGES = {
    "pending": "Your account is waiting for administrator approval.",
    "rejected": "Your registration was not approved. Please contact the administrator.",
    "suspended": "This account has been suspended. Please contact the administrator.",
}


def authenticate(email, password, ip, device_hash=None):
    email = normalize_email(email)
    wait = _rate_limit_wait(email, ip or "unknown")
    if wait:
        minutes = max(1, (wait + 59) // 60)
        return LoginResult(False, code="rate_limited", retry_after=wait,
                           message=f"Too many failed attempts. Try again in about {minutes} minute(s).")

    user = query_one(
        "SELECT id, email, password_hash, full_name, role, status FROM users WHERE email = %s",
        (email,))
    password_ok = check_password_hash(user["password_hash"] if user else _dummy_hash(), password or "")

    if user is None or not password_ok:
        with transaction():
            _record_attempt(email, ip, False)
            audit_service.log("login.failed", user_id=user["id"] if user else None,
                              details={"email": email[:190]}, ip=ip)
        return LoginResult(False, code="invalid", message="Incorrect email or password.")

    if user["status"] != "active":
        audit_service.log("login.blocked", user_id=user["id"],
                          details={"status": user["status"]}, ip=ip)
        return LoginResult(False, code=user["status"],
                           message=_STATUS_MESSAGES.get(user["status"], "This account cannot sign in."))

        blocked = None

    for attempt in range(3):
        blocked = None

        try:
            with transaction():
                if user["role"] == "student":
                    decision = device_service.on_student_login(
                        device_hash, user["id"], ip
                    )

                    if not decision.allowed:
                        blocked = decision
                        audit_service.log(
                            "login.blocked_device",
                            user_id=user["id"],
                            details={"reason": decision.code},
                            ip=ip,
                        )

                if blocked is None:
                    _record_attempt(email, ip, True)
                    execute(
                        "UPDATE users SET last_login_at = UTC_TIMESTAMP() WHERE id = %s",
                        (user["id"],),
                    )
                    execute(
                        "DELETE FROM login_attempts "
                        "WHERE attempted_at < UTC_TIMESTAMP() - INTERVAL 7 DAY"
                    )
                    audit_service.log(
                        "login.success", user_id=user["id"], ip=ip
                    )

            break

        except DatabaseError as exc:
            if getattr(exc, "errno", None) != 1213 or attempt == 2:
                raise

            time.sleep(0.025 * (attempt + 1))

    if blocked is not None:
        return LoginResult(
            False,
            code=blocked.code,
            message=blocked.message,
            retry_after=blocked.retry_after,
        )

    user.pop("password_hash")
    return LoginResult(True, user=user)


# ----------------------------------------------------------------------
# Password change (same hashing, policy and rate limiter as login)
# ----------------------------------------------------------------------
class PasswordChangeError(Exception):
    def __init__(self, errors):
        super().__init__("password change failed")
        self.errors = errors


def change_password(user_id, email, current, new, confirm, ip):
    """Verify the current password, apply the password policy, store the new hash."""
    wait = _rate_limit_wait(normalize_email(email), ip or "unknown")
    if wait:
        raise PasswordChangeError({"_form": "Too many failed attempts. Try again in a few minutes."})
    row = query_one("SELECT password_hash FROM users WHERE id = %s", (user_id,))
    errors = {}
    if row is None or not check_password_hash(row["password_hash"], current or ""):
        with transaction():                      # failures count toward the login lockout
            _record_attempt(normalize_email(email), ip, False)
            audit_service.log("password.change_failed", user_id=user_id, ip=ip)
        errors["current_password"] = "Current password is incorrect."
    problem = validate_password(new or "", email)
    if problem:
        errors["new_password"] = problem
    elif new != confirm:
        errors["confirm_password"] = "Passwords do not match."
    elif row is not None and check_password_hash(row["password_hash"], new):
        errors["new_password"] = "Choose a password different from the current one."
    if errors:
        raise PasswordChangeError(errors)
    with transaction():
        execute("UPDATE users SET password_hash = %s, updated_at = UTC_TIMESTAMP() WHERE id = %s",
                (hash_password(new), user_id))
        audit_service.log("password.change", user_id=user_id, entity_type="user", entity_id=user_id, ip=ip)
