"""Administrator actions available in Phase 2: dashboard counts and
approval of self-registered accounts. Later phases extend this module."""
from app.db import execute, query_all, query_one, transaction
from app.services import audit_service


def dashboard_counts():
    counts = query_one(
        "SELECT COALESCE(SUM(role = 'student' AND status = 'active'), 0) AS students, "
        "       COALESCE(SUM(role = 'teacher' AND status = 'active'), 0) AS teachers, "
        "       COALESCE(SUM(status = 'pending'), 0) AS pending "
        "FROM users")
    counts["departments"] = query_one("SELECT COUNT(*) AS c FROM departments")["c"]
    return counts


def list_pending_users():
    return query_all(
        "SELECT u.id, u.full_name, u.email, u.role, u.created_at, "
        "       COALESCE(sp.roll_number, tp.employee_code) AS identifier, "
        "       d.name AS department "
        "FROM users u "
        "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "LEFT JOIN teacher_profiles tp ON tp.user_id = u.id "
        "LEFT JOIN departments d ON d.id = COALESCE(sp.department_id, tp.department_id) "
        "WHERE u.status = 'pending' ORDER BY u.created_at")


def review_pending_user(admin_id, user_id, new_status, ip):
    """Approve ('active') or reject ('rejected') a pending student/teacher.

    Returns True if a pending account was changed. The WHERE clause makes
    the change atomic: an account already reviewed cannot be reviewed again.
    """
    if new_status not in ("active", "rejected"):
        raise ValueError("new_status must be 'active' or 'rejected'")
    with transaction():
        changed = execute(
            "UPDATE users SET status = %s, approved_by = %s, approved_at = UTC_TIMESTAMP(), "
            "updated_at = UTC_TIMESTAMP() "
            "WHERE id = %s AND status = 'pending' AND role IN ('student', 'teacher')",
            (new_status, admin_id, user_id)).rowcount
        if changed:
            audit_service.log("user.approve" if new_status == "active" else "user.reject",
                              user_id=admin_id, entity_type="user", entity_id=user_id, ip=ip)
    return bool(changed)
def list_users(search="", role="", status=""):
    """Return users matching the supplied admin filters."""
    allowed_roles = ("student", "teacher", "admin")
    allowed_statuses = ("pending", "active", "suspended", "rejected")

    conditions = []
    params = []

    if search:
        conditions.append("(u.full_name LIKE %s OR u.email LIKE %s)")
        term = f"%{search}%"
        params.extend([term, term])

    if role in allowed_roles:
        conditions.append("u.role = %s")
        params.append(role)

    if status in allowed_statuses:
        conditions.append("u.status = %s")
        params.append(status)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    return query_all(
        "SELECT u.id, u.full_name, u.email, u.role, u.status, u.created_at, "
        "COALESCE(sp.roll_number, tp.employee_code) AS identifier "
        "FROM users u "
        "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "LEFT JOIN teacher_profiles tp ON tp.user_id = u.id "
        + where +
        " ORDER BY u.created_at DESC, u.id DESC",
        tuple(params)
    )


def update_user_status(admin_id, user_id, new_status, ip):
    """Allow admins to activate or suspend accounts, but not themselves."""
    if new_status not in ("active", "suspended"):
        raise ValueError("Invalid account status.")

    if admin_id == user_id:
        return False

    with transaction():
        changed = execute(
            "UPDATE users SET status = %s, updated_at = UTC_TIMESTAMP() "
            "WHERE id = %s AND role IN ('student', 'teacher') "
            "AND status IN ('active', 'suspended')",
            (new_status, user_id)
        ).rowcount

        if changed:
            audit_service.log(
                "user.activate" if new_status == "active" else "user.suspend",
                user_id=admin_id,
                entity_type="user",
                entity_id=user_id,
                details={"new_status": new_status},
                ip=ip
            )

    return bool(changed)
def list_departments():
    return query_all(
        "SELECT d.id, d.code, d.name, "
        "COUNT(DISTINCT c.id) AS class_count "
        "FROM departments d "
        "LEFT JOIN classes c ON c.department_id = d.id "
        "GROUP BY d.id, d.code, d.name "
        "ORDER BY d.name"
    )


def list_academic_years():
    return query_all(
        "SELECT id, label, start_date, end_date, is_current "
        "FROM academic_years "
        "ORDER BY start_date DESC"
    )


def list_classes():
    return query_all(
        "SELECT c.id, c.name, c.section, "
        "d.id AS department_id, d.name AS department_name, d.code, "
        "ay.id AS academic_year_id, ay.label AS academic_year "
        "FROM classes c "
        "JOIN departments d ON d.id = c.department_id "
        "JOIN academic_years ay ON ay.id = c.academic_year_id "
        "ORDER BY ay.start_date DESC, d.name, c.name, c.section"
    )


def create_department(code, name, admin_id, ip):
    result = execute(
        "INSERT INTO departments (code, name) VALUES (%s, %s)",
        (code, name)
    )
    audit_service.log(
        "department.create",
        user_id=admin_id,
        entity_type="department",
        entity_id=result.lastrowid,
        details={"code": code, "name": name},
        ip=ip
    )
    return result.lastrowid


def update_department(department_id, code, name, admin_id, ip):
    with transaction():
        changed = execute(
            "UPDATE departments SET code = %s, name = %s WHERE id = %s",
            (code, name, department_id)
        ).rowcount

        if changed:
            audit_service.log(
                "department.update",
                user_id=admin_id,
                entity_type="department",
                entity_id=department_id,
                details={"code": code, "name": name},
                ip=ip
            )

    return bool(changed)


def create_class(department_id, academic_year_id, name, section, admin_id, ip):
    result = execute(
        "INSERT INTO classes "
        "(department_id, academic_year_id, name, section) "
        "VALUES (%s, %s, %s, %s)",
        (department_id, academic_year_id, name, section)
    )
    audit_service.log(
        "class.create",
        user_id=admin_id,
        entity_type="class",
        entity_id=result.lastrowid,
        details={"name": name, "section": section},
        ip=ip
    )
    return result.lastrowid


def update_class(class_id, department_id, academic_year_id,
                 name, section, admin_id, ip):
    with transaction():
        changed = execute(
            "UPDATE classes SET department_id = %s, academic_year_id = %s, "
            "name = %s, section = %s WHERE id = %s",
            (department_id, academic_year_id, name, section, class_id)
        ).rowcount

        if changed:
            audit_service.log(
                "class.update",
                user_id=admin_id,
                entity_type="class",
                entity_id=class_id,
                details={"name": name, "section": section},
                ip=ip
            )

    return bool(changed)


def list_admin_classrooms():
    return query_all(
        "SELECT id, name, building, latitude, longitude, default_radius_m "
        "FROM classrooms ORDER BY name"
    )


def create_classroom(name, building, latitude, longitude,
                     radius, admin_id, ip):
    with transaction():
        result = execute(
            "INSERT INTO classrooms "
            "(name, building, latitude, longitude, default_radius_m) "
            "VALUES (%s, %s, %s, %s, %s)",
            (name, building or None, latitude, longitude, radius)
        )

        audit_service.log(
            "classroom.create",
            user_id=admin_id,
            entity_type="classroom",
            entity_id=result.lastrowid,
            details={
                "name": name,
                "building": building,
                "default_radius_m": radius
            },
            ip=ip
        )

    return result.lastrowid


def update_classroom(classroom_id, name, building,
                     latitude, longitude, radius, admin_id, ip):
    with transaction():
        changed = execute(
            "UPDATE classrooms SET name = %s, building = %s, "
            "latitude = %s, longitude = %s, default_radius_m = %s "
            "WHERE id = %s",
            (name, building or None, latitude, longitude,
             radius, classroom_id)
        ).rowcount

        if changed:
            audit_service.log(
                "classroom.update",
                user_id=admin_id,
                entity_type="classroom",
                entity_id=classroom_id,
                details={
                    "name": name,
                    "building": building,
                    "default_radius_m": radius
                },
                ip=ip
            )

    return bool(changed)
