"""Teacher results entry. Uses the SAME default grading scheme and the same credit/grade-point
snapshots as the Phase 4 student calculations; there is no second formula here.

* Marks are the input; the grade and grade points are DERIVED on the server from the scheme
  (highest band whose minimum percentage is <= marks/max x 100). The browser never supplies a grade.
* Rows start as drafts (is_published = 0) and can be edited freely by the teacher. Publishing makes
  them official; a published result cannot be changed by a teacher (an administrator must unpublish it
  with a reason, which is audited). Students never see drafts.
* One row per (student, subject, semester) is guaranteed by the database unique key; saves are upserts.
* Whether teachers may publish at all is the TEACHER_CAN_PUBLISH_RESULTS setting.
"""
from decimal import Decimal, InvalidOperation

from flask import current_app

from app import clock
from app.db import execute, query_all, transaction
from app.services import academic_service, audit_service, report_service, teacher_service
from app.services.errors import ServiceError

MAX_MARKS_LIMIT = Decimal("1000")
CENT = Decimal("0.01")


def grade_for_percent(rules, percent):
    """The scheme band for a percentage, or None. Rules need min_percent and are checked highest first."""
    for rule in sorted(rules, key=lambda r: Decimal(r["min_percent"]), reverse=True):
        if percent >= Decimal(rule["min_percent"]):
            return rule
    return None


def parse_marks(raw_marks, raw_max):
    """-> (marks, max_marks) Decimals, or a message string. Blank marks -> (None, None)."""
    if raw_marks is None or str(raw_marks).strip() == "":
        return None, None
    try:
        marks = Decimal(str(raw_marks).strip())
        maximum = Decimal(str(raw_max).strip()) if raw_max not in (None, "") else Decimal("100")
    except InvalidOperation:
        return "Enter numbers only."
    if not marks.is_finite() or not maximum.is_finite():
        return "Enter numbers only."
    if maximum <= 0 or maximum > MAX_MARKS_LIMIT:
        return f"Maximum marks must be above 0 and at most {MAX_MARKS_LIMIT}."
    if marks < 0 or marks > maximum:
        return f"Marks must be between 0 and {maximum}."
    if marks != marks.quantize(CENT) or maximum != maximum.quantize(CENT):
        return "Use at most two decimal places."
    return marks, maximum


def get_grid(teacher_id, assignment_id):
    a = teacher_service.get_assignment(teacher_id, assignment_id)
    students = query_all(
        "SELECT u.id, u.full_name, sp.roll_number, r.marks_obtained, r.max_marks, r.grade_label, r.grade_point, "
        "       r.is_published, r.entered_at "
        "FROM enrollments e JOIN users u ON u.id = e.student_id LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "LEFT JOIN academic_results r ON r.student_id = u.id AND r.subject_id = e.subject_id AND r.semester_id = e.semester_id "
        "WHERE e.subject_id = %s AND e.class_id = %s AND e.semester_id = %s AND e.status = 'active' "
        "ORDER BY sp.roll_number, u.full_name", (a["subject_id"], a["class_id"], a["semester_id"]))
    scheme = academic_service.default_scheme()
    return {"assignment": a, "students": students, "scheme": scheme,
            "drafts": sum(1 for s in students if s["grade_label"] and not s["is_published"]),
            "published": sum(1 for s in students if s["is_published"]),
            "without_result": sum(1 for s in students if not s["grade_label"]),
            "can_publish": bool(current_app.config["TEACHER_CAN_PUBLISH_RESULTS"])}


def save_results(teacher_id, assignment_id, entries, publish, ip):
    """entries: [{'student_id', 'marks', 'max_marks'}]. All-or-nothing. Returns a summary dict."""
    if publish and not current_app.config["TEACHER_CAN_PUBLISH_RESULTS"]:
        raise ServiceError("publish_not_allowed", "Only administrators may publish results at this institution.", 403)
    a = teacher_service.get_assignment(teacher_id, assignment_id)
    scheme = academic_service._require_scheme()
    rules = scheme["rules"]
    enrolled = {r["student_id"] for r in query_all(
        "SELECT student_id FROM enrollments WHERE subject_id = %s AND class_id = %s AND semester_id = %s "
        "AND status = 'active'", (a["subject_id"], a["class_id"], a["semester_id"]))}
    existing = {r["student_id"]: r for r in query_all(
        "SELECT id, student_id, is_published FROM academic_results WHERE subject_id = %s AND semester_id = %s",
        (a["subject_id"], a["semester_id"]))}
    errors, todo = {}, []
    for entry in entries:
        sid = entry.get("student_id")
        key = f"marks.{sid}"
        if sid not in enrolled:
            errors[key] = "This student is not in the class."
            continue
        parsed = parse_marks(entry.get("marks"), entry.get("max_marks"))
        if isinstance(parsed, str):
            errors[key] = parsed
            continue
        if parsed == (None, None):
            continue
        if existing.get(sid, {}).get("is_published"):
            errors[key] = "Already published; ask an administrator to unpublish it first."
            continue
        marks, maximum = parsed
        rule = grade_for_percent(rules, marks / maximum * 100)
        if rule is None:
            errors[key] = "No grade band matches these marks."
            continue
        todo.append((sid, marks, maximum, rule))
    if errors:
        raise ServiceError("validation_error", "Some marks could not be saved. Nothing was changed.", 422, fields=errors)

    now = clock.now()
    with transaction():
        for sid, marks, maximum, rule in todo:
            if sid in existing:
                execute("UPDATE academic_results SET scheme_id = %s, marks_obtained = %s, max_marks = %s, grade_label = %s, "
                        "grade_point = %s, credits_snapshot = %s, entered_by = %s, entered_at = %s "
                        "WHERE id = %s AND is_published = 0",
                        (scheme["id"], marks, maximum, rule["grade_label"], rule["grade_point"], a["credits"],
                         teacher_id, now, existing[sid]["id"]))
            else:
                execute("INSERT INTO academic_results (student_id, subject_id, semester_id, scheme_id, marks_obtained, "
                        "max_marks, grade_label, grade_point, credits_snapshot, is_published, entered_by, entered_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s)",
                        (sid, a["subject_id"], a["semester_id"], scheme["id"], marks, maximum, rule["grade_label"],
                         rule["grade_point"], a["credits"], teacher_id, now))
        published = 0
        if publish:
            ids = [r["student_id"] for r in query_all(
                "SELECT r.student_id FROM academic_results r JOIN enrollments e ON e.student_id = r.student_id "
                "AND e.subject_id = r.subject_id AND e.semester_id = r.semester_id AND e.class_id = %s "
                "AND e.status = 'active' WHERE r.subject_id = %s AND r.semester_id = %s AND r.is_published = 0",
                (a["class_id"], a["subject_id"], a["semester_id"]))]
            if ids:
                in_clause = ",".join(["%s"] * len(ids))
                published = execute(
                    "UPDATE academic_results SET is_published = 1, entered_by = %s, entered_at = %s "
                    f"WHERE subject_id = %s AND semester_id = %s AND is_published = 0 AND student_id IN ({in_clause})",
                    (teacher_id, now, a["subject_id"], a["semester_id"], *ids)).rowcount
        audit_service.log("result.save", user_id=teacher_id, entity_type="teacher_subject", entity_id=assignment_id, ip=ip,
                          details={"subject_id": a["subject_id"], "semester_id": a["semester_id"], "saved": len(todo)})
        if publish:
            audit_service.log("result.publish", user_id=teacher_id, entity_type="teacher_subject", entity_id=assignment_id,
                              ip=ip, details={"subject_id": a["subject_id"], "semester_id": a["semester_id"],
                                              "published": published})
    return {"saved": len(todo), "published": published}


def export_csv(teacher_id, assignment_id):
    grid = get_grid(teacher_id, assignment_id)
    a = grid["assignment"]
    rows = [[s["roll_number"], s["full_name"], s["marks_obtained"], s["max_marks"], s["grade_label"], s["grade_point"],
             "" if not s["grade_label"] else ("Published" if s["is_published"] else "Draft")] for s in grid["students"]]
    name = f"results_{a['subject_code']}_{a['class_name']}{a['section']}.csv".replace(" ", "_")
    return name, report_service.rows_to_csv(["Roll number", "Student", "Marks", "Out of", "Grade", "Grade points", "Status"], rows)
