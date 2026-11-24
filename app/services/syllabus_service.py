"""Semester-wise syllabus and learning materials.

Access policy (documented): the syllabus of any semester in the viewer's
department is readable. Students may only see/download learning materials
for semesters up to and including their current semester, and only
published ones. Teachers and admins may access every published material.
Phase 5 adds the upload/management interfaces; the file store layout
(instance/uploads/materials/<uuid hex>.pdf) is the integration point.
"""
from app.db import query_all, query_one


def semester_numbers(ctx):
    return list(range(1, 9))


def semester_view(ctx, semester_number, role):
    subjects = query_all(
        "SELECT id, code, name, credits FROM subjects WHERE department_id = %s AND semester_number = %s "
        "AND is_active = 1 ORDER BY code", (ctx["department_id"], semester_number))
    units = query_all(
        "SELECT sy.subject_id, sy.unit_number, sy.title, sy.topics, sy.lecture_hours, sy.learning_outcomes, "
        "       sy.reference_books, sy.updated_at "
        "FROM syllabus sy JOIN subjects sub ON sub.id = sy.subject_id "
        "WHERE sub.department_id = %s AND sub.semester_number = %s AND sy.is_published = 1 "
        "ORDER BY sy.subject_id, sy.unit_number",
        (ctx["department_id"], semester_number))
    can_see_materials = role != "student" or (ctx.get("semester_number") or 0) >= semester_number
    materials = query_all(
        "SELECT m.id, m.subject_id, m.title, m.size_bytes, m.uploaded_at FROM learning_materials m "
        "JOIN subjects sub ON sub.id = m.subject_id "
        "WHERE sub.department_id = %s AND sub.semester_number = %s AND m.is_published = 1 "
        "ORDER BY m.uploaded_at DESC, m.id DESC", (ctx["department_id"], semester_number)) if can_see_materials else []
    for s in subjects:
        s["units"] = [u for u in units if u["subject_id"] == s["id"]]
        s["materials"] = [m for m in materials if m["subject_id"] == s["id"]]
    return {"semester_number": semester_number, "subjects": subjects,
            "materials_hidden": not can_see_materials}


def material_for_download(ctx, role, material_id):
    """The material row if this viewer may download it, else None (-> 404)."""
    row = query_one(
        "SELECT m.id, m.title, m.original_filename, m.stored_filename, sub.department_id, sub.semester_number "
        "FROM learning_materials m JOIN subjects sub ON sub.id = m.subject_id "
        "WHERE m.id = %s AND m.is_published = 1", (material_id,))
    if row is None:
        return None
    if role == "student":
        if row["department_id"] != ctx["department_id"] or row["semester_number"] > (ctx.get("semester_number") or 0):
            return None
    elif role == "teacher" and row["department_id"] != ctx["department_id"]:
        return None
    return row
