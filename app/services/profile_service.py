"""Viewer context and student profile queries (dashboards live in portal_service / teacher_service)."""
from app.db import query_one


# ---------------------------------------------------------------- Phase 4
import re  # noqa: E402

from app.db import execute, transaction  # noqa: E402
from app.services import audit_service  # noqa: E402
from app.services.errors import ServiceError  # noqa: E402

PHONE_RE = re.compile(r"^[0-9+()\- ]{7,20}$")


def get_context(user):
    """Who is looking: role plus the department/semester that scope what they may see."""
    ctx = {"role": user["role"], "student_id": user["id"], "department_id": None,
           "semester_number": None, "semester_id": None, "class_id": None}
    if user["role"] == "student":
        row = query_one("SELECT sp.department_id, sp.current_semester_id, sp.class_id, s.semester_number "
                        "FROM student_profiles sp LEFT JOIN semesters s ON s.id = sp.current_semester_id "
                        "WHERE sp.user_id = %s", (user["id"],))
        if row:
            ctx.update(department_id=row["department_id"], semester_id=row["current_semester_id"],
                       semester_number=row["semester_number"], class_id=row["class_id"])
    elif user["role"] == "teacher":
        row = query_one("SELECT department_id FROM teacher_profiles WHERE user_id = %s", (user["id"],))
        if row:
            ctx["department_id"] = row["department_id"]
    return ctx


def get_profile_details(student_id):
    return query_one(
        "SELECT u.full_name, u.email, u.status, sp.roll_number, sp.phone, sp.admission_year, "
        "       d.name AS department, c.name AS class_name, c.section, s.name AS semester_name, "
        "       s.semester_number, ay.label AS year_label "
        "FROM users u JOIN student_profiles sp ON sp.user_id = u.id "
        "JOIN departments d ON d.id = sp.department_id "
        "LEFT JOIN classes c ON c.id = sp.class_id "
        "LEFT JOIN semesters s ON s.id = sp.current_semester_id "
        "LEFT JOIN academic_years ay ON ay.id = s.academic_year_id "
        "WHERE u.id = %s", (student_id,))


def update_phone(student_id, phone, ip):
    """The ONLY profile field a student may change. Name, roll number, department,
    class, semester, role and status are managed by administrators."""
    phone = (phone or "").strip()
    if phone and not PHONE_RE.match(phone):
        raise ServiceError("validation_error", "Enter a valid phone number (7 to 20 digits, + ( ) - allowed).",
                           422, fields={"phone": "Enter a valid phone number."})
    with transaction():
        execute("UPDATE student_profiles SET phone = %s WHERE user_id = %s", (phone or None, student_id))
        audit_service.log("profile.update_phone", user_id=student_id, entity_type="user",
                          entity_id=student_id, ip=ip)
