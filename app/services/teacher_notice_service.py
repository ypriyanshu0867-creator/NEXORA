"""Teacher notices. SCOPE RULES
* A teacher may only notify a class (and optionally a semester) they are assigned to; the notice is
  addressed to students of the teacher's own department. No institution-wide or department-wide notices.
* Allowed categories: general, exam, assignment, timetable, material. Holiday, event, fee, library and
  result notices stay under administrative control.
* A teacher can edit and publish/unpublish only their OWN notices. Drafts (is_active = 0) are never
  shown to students. Times entered are UTC.
"""
import datetime

from app import clock
from app.db import execute, query_all, query_one, transaction
from app.services import audit_service, upload_service
from app.services.errors import ServiceError

TEACHER_CATEGORIES = ("general", "exam", "assignment", "timetable", "material")
LABELS = {"general": "General / class announcement", "exam": "Examination", "assignment": "Assignment deadline",
          "timetable": "Timetable update", "material": "Learning material"}


def scope_options(teacher_id):
    """Classes the teacher may address, with the semesters they teach each in."""
    rows = query_all("SELECT DISTINCT c.id AS class_id, c.name, c.section, sem.semester_number "
                     "FROM teacher_subjects ts JOIN classes c ON c.id = ts.class_id "
                     "JOIN semesters sem ON sem.id = ts.semester_id WHERE ts.teacher_id = %s "
                     "ORDER BY c.name, c.section, sem.semester_number", (teacher_id,))
    classes = {}
    for r in rows:
        entry = classes.setdefault(r["class_id"], {"class_id": r["class_id"], "label": f"{r['name']} {r['section']}", "semesters": []})
        entry["semesters"].append(r["semester_number"])
    return list(classes.values())


def list_own(teacher_id):
    return query_all("SELECT n.id, n.title, n.category, n.is_active, n.created_at, n.published_at, n.expires_at, n.deadline_at, "
                     "n.semester_number, c.name AS class_name, c.section, (n.attachment_stored_name IS NOT NULL) AS has_attachment "
                     "FROM notices n LEFT JOIN classes c ON c.id = n.class_id WHERE n.created_by = %s "
                     "ORDER BY n.created_at DESC, n.id DESC LIMIT 100", (teacher_id,))


def get_own(teacher_id, notice_id):
    row = query_one("SELECT id, title, body, category, class_id, semester_number, deadline_at, expires_at, is_active, "
                    "attachment_original_name, (attachment_stored_name IS NOT NULL) AS has_attachment "
                    "FROM notices WHERE id = %s AND created_by = %s", (notice_id, teacher_id))
    if row is None:
        raise ServiceError("not_found", "Notice not found.", 404)
    return row


def _when(value, field, errors, end_of_day=False):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            moment = datetime.datetime.strptime(value, fmt)
            return moment.replace(hour=23, minute=59, second=59) if end_of_day and fmt == "%Y-%m-%d" else moment
        except ValueError:
            continue
    errors[field] = "Enter a valid date."
    return None


def clean(teacher_id, data):
    errors = {}
    title, body = (data.get("title") or "").strip(), (data.get("body") or "").strip()
    if not 3 <= len(title) <= 180:
        errors["title"] = "Enter a title (3 to 180 characters)."
    if not 1 <= len(body) <= 5000:
        errors["body"] = "Enter the notice text (up to 5000 characters)."
    category = data.get("category")
    if category not in TEACHER_CATEGORIES:
        errors["category"] = "Choose a permitted category."
    scope = {c["class_id"]: c for c in scope_options(teacher_id)}
    try:
        class_id = int(data.get("class_id"))
    except (TypeError, ValueError):
        class_id = None
    if class_id not in scope:
        errors["class_id"] = "Choose one of your classes."
        class_id = None
    semester = None
    if str(data.get("semester_number") or "").strip():
        try:
            semester = int(data["semester_number"])
        except (TypeError, ValueError):
            semester = None
        if class_id is not None and semester not in scope[class_id]["semesters"]:
            errors["semester_number"] = "You do not teach this class in that semester."
    deadline = _when(data.get("deadline_at"), "deadline_at", errors)
    expires = _when(data.get("expires_at"), "expires_at", errors, end_of_day=True)
    if expires is not None and expires <= clock.now():
        errors["expires_at"] = "The expiry must be in the future."
    if errors:
        raise ServiceError("validation_error", "Please correct the highlighted fields.", 422, fields=errors)
    return {"title": title, "body": body, "category": category, "class_id": class_id, "semester_number": semester,
            "deadline_at": deadline, "expires_at": expires}


def save_notice(teacher_id, data, notice_id, file_storage, publish, ip):
    """publish: True publishes, False saves as draft/unpublishes, None keeps the current state."""
    c = clean(teacher_id, data)
    dept = query_one("SELECT department_id FROM teacher_profiles WHERE user_id = %s", (teacher_id,))
    if dept is None:
        raise ServiceError("forbidden", "Your teacher profile is incomplete.", 403)
    saved = upload_service.save_pdf(file_storage, "notices") if file_storage is not None and getattr(file_storage, "filename", "") else None
    old_file = None
    now = clock.now()
    try:
        with transaction():
            if notice_id is None:
                active = 1 if publish else 0
                notice_id = execute(
                    "INSERT INTO notices (title, body, category, audience, department_id, semester_number, class_id, deadline_at, "
                    "is_active, created_by, created_at, published_at, expires_at, is_pinned, attachment_stored_name, "
                    "attachment_original_name) VALUES (%s, %s, %s, 'student', %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s)",
                    (c["title"], c["body"], c["category"], dept["department_id"], c["semester_number"], c["class_id"],
                     c["deadline_at"], active, teacher_id, now, now if active else None, c["expires_at"],
                     saved and saved["stored_name"], saved and saved["original_name"])).lastrowid
                action = "notice.publish" if publish else "notice.create"
            else:
                row = query_one("SELECT is_active, published_at, attachment_stored_name FROM notices "
                                "WHERE id = %s AND created_by = %s FOR UPDATE", (notice_id, teacher_id))
                if row is None:
                    raise ServiceError("not_found", "Notice not found.", 404)
                active = row["is_active"] if publish is None else (1 if publish else 0)
                published_at = row["published_at"] if (row["is_active"] and active) else (now if active else row["published_at"])
                old_file = row["attachment_stored_name"] if saved else None
                execute("UPDATE notices SET title = %s, body = %s, category = %s, department_id = %s, semester_number = %s, "
                        "class_id = %s, deadline_at = %s, expires_at = %s, is_active = %s, published_at = %s"
                        + (", attachment_stored_name = %s, attachment_original_name = %s" if saved else "") +
                        " WHERE id = %s AND created_by = %s",
                        (c["title"], c["body"], c["category"], dept["department_id"], c["semester_number"], c["class_id"],
                         c["deadline_at"], c["expires_at"], active, published_at,
                         *((saved["stored_name"], saved["original_name"]) if saved else ()), notice_id, teacher_id))
                action = "notice.update"
            audit_service.log(action, user_id=teacher_id, entity_type="notice", entity_id=notice_id, ip=ip,
                              details={"category": c["category"], "class_id": c["class_id"], "published": bool(active)})
    except Exception:
        if saved:
            upload_service.remove_file("notices", saved["stored_name"])
        raise
    if old_file:
        upload_service.remove_file("notices", old_file)
    return notice_id


def set_published(teacher_id, notice_id, published, ip):
    now = clock.now()
    with transaction():
        row = query_one("SELECT is_active, published_at FROM notices WHERE id = %s AND created_by = %s FOR UPDATE",
                        (notice_id, teacher_id))
        if row is None:
            raise ServiceError("not_found", "Notice not found.", 404)
        execute("UPDATE notices SET is_active = %s, published_at = %s WHERE id = %s",
                (1 if published else 0, now if (published and not row["is_active"]) else row["published_at"], notice_id))
        audit_service.log("notice.publish" if published else "notice.unpublish", user_id=teacher_id,
                          entity_type="notice", entity_id=notice_id, ip=ip)
