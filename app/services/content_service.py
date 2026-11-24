"""Teacher syllabus and learning-material management.

A teacher may manage content only for subjects they are assigned to (any class). Units can be
created, edited and published/hidden (never deleted, so history is kept); materials are validated
PDFs that can be published or hidden. Every change writes an audit entry carrying the subject id,
which also powers the "publication history" panel. Students see only published items (Phase 4).
"""
from app import clock
from app.db import IntegrityError, execute, is_duplicate_key, query_all, query_one, transaction
from app.services import audit_service, upload_service
from app.services.errors import ServiceError


def require_subject(teacher_id, subject_id):
    row = query_one("SELECT sub.id, sub.code, sub.name, sub.semester_number, sub.department_id FROM subjects sub "
                    "WHERE sub.id = %s AND sub.id IN (SELECT subject_id FROM teacher_subjects WHERE teacher_id = %s)",
                    (subject_id, teacher_id))
    if row is None:
        raise ServiceError("not_found", "That subject was not found.", 404)
    return row


def assigned_subjects(teacher_id):
    return query_all(
        "SELECT sub.id, sub.code, sub.name, sub.semester_number, "
        "  (SELECT COUNT(*) FROM syllabus sy WHERE sy.subject_id = sub.id) AS units, "
        "  (SELECT COUNT(*) FROM learning_materials m WHERE m.subject_id = sub.id) AS materials "
        "FROM subjects sub WHERE sub.id IN (SELECT subject_id FROM teacher_subjects WHERE teacher_id = %s) "
        "ORDER BY sub.semester_number, sub.code", (teacher_id,))


def overview(teacher_id, subject_id):
    subject = require_subject(teacher_id, subject_id)
    return {"subject": subject,
            "units": query_all("SELECT id, unit_number, title, topics, lecture_hours, learning_outcomes, reference_books, "
                               "is_published, updated_at FROM syllabus WHERE subject_id = %s ORDER BY unit_number", (subject_id,)),
            "materials": query_all("SELECT m.id, m.title, m.description, m.original_filename, m.size_bytes, m.uploaded_at, "
                                   "m.is_published, u.full_name AS uploader FROM learning_materials m "
                                   "LEFT JOIN users u ON u.id = m.uploaded_by WHERE m.subject_id = %s "
                                   "ORDER BY m.uploaded_at DESC, m.id DESC", (subject_id,)),
            "history": history(subject_id)}


def history(subject_id, limit=15):
    return query_all(
        "SELECT a.created_at, a.action, u.full_name AS actor, a.details FROM audit_logs a "
        "LEFT JOIN users u ON u.id = a.user_id "
        "WHERE a.entity_type IN ('syllabus', 'learning_material') "
        "AND JSON_UNQUOTE(JSON_EXTRACT(a.details, '$.subject_id')) = %s ORDER BY a.id DESC LIMIT %s",
        (str(subject_id), limit))


def _clean_unit(data):
    errors = {}
    def text(key, maximum, required=False):
        value = (data.get(key) or "").strip()
        if required and not value:
            errors[key] = "Required."
        elif len(value) > maximum:
            errors[key] = f"At most {maximum} characters."
        return value or None
    try:
        number = int(str(data.get("unit_number", "")).strip())
        if not 1 <= number <= 30:
            raise ValueError
    except ValueError:
        number = None
        errors["unit_number"] = "Enter a unit number from 1 to 30."
    hours = None
    if str(data.get("lecture_hours", "")).strip() != "":
        try:
            hours = int(str(data["lecture_hours"]).strip())
            if not 0 <= hours <= 255:
                raise ValueError
        except ValueError:
            errors["lecture_hours"] = "Enter 0 to 255."
    cleaned = {"unit_number": number, "title": text("title", 150, True), "topics": text("topics", 5000, True),
               "lecture_hours": hours, "learning_outcomes": text("learning_outcomes", 3000),
               "reference_books": text("reference_books", 2000), "is_published": bool(data.get("is_published"))}
    if errors:
        raise ServiceError("validation_error", "Please correct the highlighted fields.", 422, fields=errors)
    return cleaned


def save_unit(teacher_id, subject_id, data, unit_id, ip):
    require_subject(teacher_id, subject_id)
    c = _clean_unit(data)
    now = clock.now()
    try:
        with transaction():
            if unit_id is None:
                unit_id = execute(
                    "INSERT INTO syllabus (subject_id, unit_number, title, topics, lecture_hours, learning_outcomes, "
                    "reference_books, is_published, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (subject_id, c["unit_number"], c["title"], c["topics"], c["lecture_hours"], c["learning_outcomes"],
                     c["reference_books"], 1 if c["is_published"] else 0, now)).lastrowid
                action = "syllabus.create"
            else:
                changed = execute(
                    "UPDATE syllabus SET unit_number = %s, title = %s, topics = %s, lecture_hours = %s, learning_outcomes = %s, "
                    "reference_books = %s, is_published = %s, updated_at = %s WHERE id = %s AND subject_id = %s",
                    (c["unit_number"], c["title"], c["topics"], c["lecture_hours"], c["learning_outcomes"],
                     c["reference_books"], 1 if c["is_published"] else 0, now, unit_id, subject_id)).rowcount
                if not changed and not query_one("SELECT id FROM syllabus WHERE id = %s AND subject_id = %s", (unit_id, subject_id)):
                    raise ServiceError("not_found", "Unit not found.", 404)
                action = "syllabus.update"
            audit_service.log(action, user_id=teacher_id, entity_type="syllabus", entity_id=unit_id, ip=ip,
                              details={"subject_id": subject_id, "unit": c["unit_number"], "published": c["is_published"]})
    except IntegrityError as exc:
        if is_duplicate_key(exc, "uq_syllabus_unit"):
            raise ServiceError("duplicate_unit", "That unit number already exists for this subject.", 409,
                               fields={"unit_number": "Already used."}) from None
        raise
    return unit_id


def set_unit_published(teacher_id, unit_id, published, ip):
    row = query_one("SELECT id, subject_id FROM syllabus WHERE id = %s", (unit_id,))
    if row is None:
        raise ServiceError("not_found", "Unit not found.", 404)
    require_subject(teacher_id, row["subject_id"])
    with transaction():
        execute("UPDATE syllabus SET is_published = %s, updated_at = %s WHERE id = %s",
                (1 if published else 0, clock.now(), unit_id))
        audit_service.log("syllabus.publish" if published else "syllabus.unpublish", user_id=teacher_id,
                          entity_type="syllabus", entity_id=unit_id, ip=ip, details={"subject_id": row["subject_id"]})
    return row["subject_id"]


def add_material(teacher_id, subject_id, title, description, file_storage, publish, ip):
    require_subject(teacher_id, subject_id)
    title, description = (title or "").strip(), (description or "").strip()
    errors = {}
    if not 3 <= len(title) <= 150:
        errors["title"] = "Enter a title (3 to 150 characters)."
    if len(description) > 255:
        errors["description"] = "At most 255 characters."
    if errors:
        raise ServiceError("validation_error", "Please correct the highlighted fields.", 422, fields=errors)
    saved = upload_service.save_pdf(file_storage, "materials")
    try:
        with transaction():
            material_id = execute(
                "INSERT INTO learning_materials (subject_id, title, description, original_filename, stored_filename, "
                "size_bytes, sha256, uploaded_by, uploaded_at, is_published) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (subject_id, title, description or None, saved["original_name"], saved["stored_name"], saved["size"],
                 saved["sha256"], teacher_id, clock.now(), 1 if publish else 0)).lastrowid
            audit_service.log("material.upload", user_id=teacher_id, entity_type="learning_material", entity_id=material_id,
                              ip=ip, details={"subject_id": subject_id, "published": bool(publish), "size": saved["size"]})
    except Exception:
        upload_service.remove_file("materials", saved["stored_name"])
        raise
    return material_id


def _material(teacher_id, material_id):
    row = query_one("SELECT id, subject_id FROM learning_materials WHERE id = %s", (material_id,))
    if row is None:
        raise ServiceError("not_found", "Material not found.", 404)
    require_subject(teacher_id, row["subject_id"])
    return row


def update_material(teacher_id, material_id, title, description, ip):
    row = _material(teacher_id, material_id)
    title, description = (title or "").strip(), (description or "").strip()
    if not 3 <= len(title) <= 150 or len(description) > 255:
        raise ServiceError("validation_error", "Title must be 3 to 150 characters; description at most 255.", 422)
    with transaction():
        execute("UPDATE learning_materials SET title = %s, description = %s WHERE id = %s", (title, description or None, material_id))
        audit_service.log("material.update", user_id=teacher_id, entity_type="learning_material", entity_id=material_id,
                          ip=ip, details={"subject_id": row["subject_id"]})
    return row["subject_id"]


def set_material_published(teacher_id, material_id, published, ip):
    row = _material(teacher_id, material_id)
    with transaction():
        execute("UPDATE learning_materials SET is_published = %s WHERE id = %s", (1 if published else 0, material_id))
        audit_service.log("material.publish" if published else "material.unpublish", user_id=teacher_id,
                          entity_type="learning_material", entity_id=material_id, ip=ip,
                          details={"subject_id": row["subject_id"]})
    return row["subject_id"]
