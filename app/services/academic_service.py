"""Results, SGPA, CGPA and the calculators. All arithmetic uses Decimal.

DEMONSTRATION POLICY (every point is configurable or replaceable; nothing
assumes one university's rules)
* Grade bands come from the DEFAULT grading scheme in the database
  (grading_schemes.is_default = 1). With no default scheme the calculators
  refuse to run and say that the institution must configure one.
* SGPA = sum(credit x grade point) / sum(credit) over one semester's
  PUBLISHED results. Subjects with 0 credits carry no weight. Failed
  subjects are included with their (usually 0) grade points.
* CGPA = the same formula over all published results, after applying the
  repeat policy (CGPA_REPEAT_POLICY): 'latest' = the most recent attempt of
  a subject replaces earlier ones, 'best' = the highest grade point counts.
  SGPAs are NEVER averaged.
* Only published results are shown to students. Unpublished marks are never
  revealed. The calculators produce ESTIMATES unless they reproduce an
  already-published official result exactly.
* The schema stores no separate "official CGPA", so the CGPA shown on the
  results page is calculated from official published results and labelled so.
"""
import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from flask import current_app

from app.db import query_all, query_one
from app.services.errors import ServiceError

MAX_CREDITS = Decimal("30")
MAX_MANUAL_ITEMS = 40
EPOCH_END = datetime.date(9999, 12, 31)


# ------------------------------------------------------------ pure maths
def round_gpa(value, places=2):
    return Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def compute_gpa(rows, places=2):
    """rows: dicts with 'credits' and 'grade_point'. Returns exact totals and the rounded GPA."""
    credits = Decimal(0)
    weighted = Decimal(0)
    for row in rows:
        c = Decimal(row["credits"])
        if c <= 0:
            continue                                   # zero-credit subjects carry no weight
        credits += c
        weighted += c * Decimal(row["grade_point"])
    gpa = None if credits == 0 else round_gpa(weighted / credits, places)
    return {"total_credits": credits, "total_weighted": weighted, "gpa": gpa}


def _attempt_key(row):
    return (row["semester_start"], row.get("entered_at") or datetime.datetime.min, row.get("id") or 0)


def apply_repeat_policy(rows, policy):
    """Keep ONE attempt per subject. Returns (counted, superseded)."""
    by_subject = {}
    for row in rows:
        by_subject.setdefault(row["subject_id"], []).append(row)
    counted, superseded = [], []
    for attempts in by_subject.values():
        if policy == "best":
            keep = max(attempts, key=lambda r: (Decimal(r["grade_point"]), _attempt_key(r)))
        else:
            keep = max(attempts, key=_attempt_key)
        counted.append(keep)
        superseded.extend(a for a in attempts if a is not keep)
    counted.sort(key=lambda r: (r["semester_start"], r["code"]))
    return counted, superseded


def result_status(row):
    if row.get("is_pass") is not None:
        return "Pass" if row["is_pass"] else "Fail"
    return "Pass" if Decimal(row["grade_point"]) > 0 else "Fail"


def _places():
    return current_app.config["GPA_DECIMALS"]


# ------------------------------------------------------------ scheme
def default_scheme():
    scheme = query_one("SELECT id, name, description FROM grading_schemes WHERE is_default = 1 "
                       "ORDER BY id LIMIT 1")
    if scheme is None:
        return None
    scheme["rules"] = query_all(
        "SELECT grade_label, min_percent, max_percent, grade_point, is_pass FROM grading_scheme_rules "
        "WHERE scheme_id = %s ORDER BY grade_point DESC, min_percent DESC", (scheme["id"],))
    return scheme if scheme["rules"] else None


def _require_scheme():
    scheme = default_scheme()
    if scheme is None:
        raise ServiceError("no_grading_scheme", "No grading scheme has been configured yet. The "
                           "institution's administrator must set a default grading scheme before "
                           "GPA can be calculated.", 409)
    return scheme


# ------------------------------------------------------------ stored results
def _published_rows(student_id):
    rows = query_all(
        "SELECT r.id, r.subject_id, r.semester_id, r.marks_obtained, r.max_marks, r.grade_label, "
        "       r.grade_point, r.credits_snapshot AS credits, r.entered_at, sub.code, sub.name, "
        "       sem.semester_number, sem.name AS semester_name, sem.start_date AS semester_start, "
        "       ay.label AS year_label, gr.is_pass "
        "FROM academic_results r "
        "JOIN subjects sub ON sub.id = r.subject_id "
        "JOIN semesters sem ON sem.id = r.semester_id "
        "JOIN academic_years ay ON ay.id = sem.academic_year_id "
        "LEFT JOIN grading_scheme_rules gr ON gr.scheme_id = r.scheme_id AND gr.grade_label = r.grade_label "
        "WHERE r.student_id = %s AND r.is_published = 1 "
        "ORDER BY sem.start_date, sub.code", (student_id,))
    for r in rows:
        r["status"] = result_status(r)
    return rows


def official_cgpa(student_id, rows=None):
    rows = _published_rows(student_id) if rows is None else rows
    policy = current_app.config["CGPA_REPEAT_POLICY"]
    counted, superseded = apply_repeat_policy(rows, policy)
    totals = compute_gpa(counted, _places())
    per_sem = {}
    for r in rows:
        per_sem.setdefault(r["semester_id"], []).append(r)
    semesters = [{"semester_name": g[0]["semester_name"], "year_label": g[0]["year_label"],
                  **compute_gpa(g, _places())}
                 for g in sorted(per_sem.values(), key=lambda g: g[0]["semester_start"])]
    return {"cgpa": totals["gpa"], "total_credits": totals["total_credits"],
            "total_weighted": totals["total_weighted"], "counted": counted, "superseded": superseded,
            "semesters": semesters, "policy": policy, "label": "Calculated from your official published results"}


def results_overview(student_id, current_semester_id=None):
    """Everything the results page shows, grouped academic year -> semester."""
    rows = _published_rows(student_id)
    years = {}
    for r in rows:
        sem = years.setdefault(r["year_label"], {}).setdefault(
            r["semester_id"], {"name": r["semester_name"], "number": r["semester_number"],
                               "start": r["semester_start"], "subjects": []})
        sem["subjects"].append(r)
    out = []
    for label in sorted(years):
        sems = []
        for sem in sorted(years[label].values(), key=lambda s: s["start"]):
            sem.update(compute_gpa(sem["subjects"], _places()))
            sem["failed"] = sum(1 for s in sem["subjects"] if s["status"] == "Fail")
            sems.append(sem)
        out.append({"year_label": label, "semesters": sems})

    published_ids = {r["semester_id"] for r in rows}
    pending = query_all(
        "SELECT DISTINCT sem.id, sem.name, sem.start_date FROM academic_results r "
        "JOIN semesters sem ON sem.id = r.semester_id "
        "WHERE r.student_id = %s AND r.is_published = 0 ORDER BY sem.start_date", (student_id,))
    names = [p["name"] for p in pending if p["id"] not in published_ids]
    if current_semester_id and current_semester_id not in published_ids:
        current = query_one("SELECT name FROM semesters WHERE id = %s", (current_semester_id,))
        if current and current["name"] not in names:
            names.append(current["name"])
    return {"years": out, "cgpa": official_cgpa(student_id, rows) if rows else None,
            "not_published": names, "has_results": bool(rows)}


# ------------------------------------------------------------ calculators
def _decimal(value, field, errors, lo, hi, allow_zero=True):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        errors[field] = "Enter a number."
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        errors[field] = "Enter a number."
        return None
    if not number.is_finite():
        errors[field] = "Enter a finite number."
        return None
    if number < lo or number > hi or (number == 0 and not allow_zero):
        errors[field] = f"Must be between {lo} and {hi}" + ("" if allow_zero else " (above 0).")
        return None
    return number


def _student_subjects(department_id, semester_number):
    return {s["id"]: s for s in query_all(
        "SELECT id, code, name, credits FROM subjects WHERE department_id = %s AND semester_number = %s "
        "AND is_active = 1 ORDER BY code", (department_id, semester_number))}


def _manual_rows(items, scheme, errors):
    if not isinstance(items, list) or not items:
        raise ServiceError("validation_error", "Add at least one subject.", 422, fields={"items": "Required."})
    if len(items) > MAX_MANUAL_ITEMS:
        raise ServiceError("validation_error", f"At most {MAX_MANUAL_ITEMS} subjects.", 422)
    top = max(Decimal(r["grade_point"]) for r in scheme["rules"])
    valid_points = {Decimal(r["grade_point"]) for r in scheme["rules"]}
    rows = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors[f"items.{i}"] = "Invalid entry."
            continue
        credits = _decimal(item.get("credits"), f"items.{i}.credits", errors, Decimal(0), MAX_CREDITS, allow_zero=False)
        point = _decimal(item.get("grade_point"), f"items.{i}.grade_point", errors, Decimal(0), top)
        if point is not None and point not in valid_points:
            errors[f"items.{i}.grade_point"] = "Not a grade point in the configured scheme."
            point = None
        if credits is not None and point is not None:
            rows.append({"code": str(item.get("name") or f"Subject {i + 1}")[:40], "name": "",
                         "credits": credits, "grade": "", "grade_point": point})
    return rows


def _graded_rows(subjects, grades, scheme, errors):
    by_label = {r["grade_label"]: r for r in scheme["rules"]}
    if not isinstance(grades, dict):
        raise ServiceError("validation_error", "Choose a grade for at least one subject.", 422)
    rows, not_included = [], []
    for sid, sub in subjects.items():
        label = grades.get(str(sid))
        if label in (None, ""):
            not_included.append({"code": sub["code"], "name": sub["name"], "reason": "No grade selected"})
            continue
        if not isinstance(label, str) or label not in by_label:
            errors[f"grades.{sid}"] = "Not a grade in the configured scheme."
            continue
        rows.append({"subject_id": sid, "code": sub["code"], "name": sub["name"], "credits": Decimal(sub["credits"]),
                     "grade": label, "grade_point": Decimal(by_label[label]["grade_point"])})
    unknown = [k for k in grades if not str(k).isdigit() or int(k) not in subjects]
    if unknown:
        errors["grades"] = "Some subjects are not part of this semester for your department."
    return rows, not_included


def _present(rows, totals, places, extra):
    for r in rows:
        r["weighted"] = Decimal(r["credits"]) * Decimal(r["grade_point"])
    return {**extra, "rows": rows, "total_credits": totals["total_credits"],
            "total_weighted": totals["total_weighted"],
            "gpa": totals["gpa"], "decimals": places}


def calculate_sgpa(ctx, payload):
    """ctx: the student's context (department_id, ...). payload: see calculator.js."""
    scheme = _require_scheme()
    errors = {}
    places = _places()
    formula = "SGPA = sum(credit x grade point) / sum(credits)"
    if "items" in payload:                                     # manual mode
        rows = _manual_rows(payload["items"], scheme, errors)
        not_included, matches = [], False
        meta = {"mode": "manual"}
    else:                                                      # subject mode
        sem_no = payload.get("semester_number")
        if isinstance(sem_no, bool) or not isinstance(sem_no, int) or not 1 <= sem_no <= 8:
            raise ServiceError("validation_error", "Choose a semester (1 to 8).", 422,
                               fields={"semester_number": "Choose a semester (1 to 8)."})
        subjects = _student_subjects(ctx["department_id"], sem_no)
        if not subjects:
            raise ServiceError("no_subjects", "No subjects are defined for that semester in your department.", 404)
        rows, not_included = _graded_rows(subjects, payload.get("grades"), scheme, errors)
        official = {r["subject_id"]: r["grade_label"] for r in _published_rows(ctx["student_id"])
                    if r["semester_number"] == sem_no}
        matches = bool(rows) and not not_included and all(official.get(r["subject_id"]) == r["grade"] for r in rows)
        meta = {"mode": "subjects", "semester_number": sem_no}
    if errors:
        raise ServiceError("validation_error", "Please correct the highlighted entries.", 422, fields=errors)
    totals = compute_gpa(rows, places)
    if not rows or totals["gpa"] is None:
        raise ServiceError("no_grades", "Select at least one grade for a subject that carries credits.", 422)
    estimate = not matches
    return _present(rows, totals, places, {
        **meta, "scheme": {"id": scheme["id"], "name": scheme["name"]}, "formula": formula,
        "is_estimate": estimate, "not_included": not_included,
        "label": "Estimate (not an official result)" if estimate else "Matches your official published result"})


def calculate_cgpa(ctx, payload):
    scheme = _require_scheme()
    errors = {}
    places = _places()
    policy = current_app.config["CGPA_REPEAT_POLICY"]
    formula = ("CGPA = sum(credit x grade point) / sum(credits) over every counted subject "
               "(semester GPAs are not averaged)")
    superseded, semesters = [], []
    if "items" in payload:                                     # manual cumulative entry
        rows = _manual_rows(payload["items"], scheme, errors)
        meta, estimate = {"mode": "manual"}, True
        counted = rows
    else:
        official = _published_rows(ctx["student_id"])
        rows = list(official)
        estimate = False
        meta = {"mode": "official"}
        hypo = payload.get("hypothetical")
        if hypo is not None:
            if not isinstance(hypo, dict):
                raise ServiceError("validation_error", "Invalid 'hypothetical' entry.", 422)
            sem_no = hypo.get("semester_number")
            if isinstance(sem_no, bool) or not isinstance(sem_no, int) or not 1 <= sem_no <= 8:
                raise ServiceError("validation_error", "Choose a semester (1 to 8).", 422,
                                   fields={"semester_number": "Choose a semester (1 to 8)."})
            subjects = _student_subjects(ctx["department_id"], sem_no)
            graded, _ = _graded_rows(subjects, hypo.get("grades"), scheme, errors)
            for g in graded:
                g.update({"semester_start": EPOCH_END, "entered_at": None, "id": 0,
                          "semester_name": f"Semester {sem_no} (hypothetical)"})
                rows.append(g)
            estimate = bool(graded)
            meta = {"mode": "official+hypothetical", "semester_number": sem_no}
        if not rows and not errors:
            raise ServiceError("no_data", "You have no published results yet, and nothing was entered to "
                               "estimate. Enter grades for a semester or use manual entry.", 404)
        counted, superseded = apply_repeat_policy(rows, policy) if rows else ([], [])
        semesters = official_cgpa(ctx["student_id"], official)["semesters"] if official else []
    if errors:
        raise ServiceError("validation_error", "Please correct the highlighted entries.", 422, fields=errors)
    totals = compute_gpa(counted, places)
    if totals["gpa"] is None:
        raise ServiceError("no_grades", "No subject with credits to calculate from.", 422)
    return _present(counted, totals, places, {
        **meta, "scheme": {"id": scheme["id"], "name": scheme["name"]}, "formula": formula, "policy": policy,
        "superseded": [{"code": s["code"], "semester": s.get("semester_name", ""), "grade": s.get("grade_label", s.get("grade", ""))}
                       for s in superseded],
        "semesters": semesters, "is_estimate": estimate,
        "label": "Estimate (includes entries that are not official results)" if estimate
                 else "Calculated from your official published results"})


def calculator_data(ctx):
    """Scheme, grade choices and the subjects per semester, for the calculator page."""
    scheme = default_scheme()
    subjects = query_all("SELECT id, code, name, credits, semester_number FROM subjects "
                         "WHERE department_id = %s AND is_active = 1 ORDER BY semester_number, code",
                         (ctx["department_id"],))
    return {"scheme": None if scheme is None else {
                "name": scheme["name"],
                "grades": [{"label": r["grade_label"], "grade_point": r["grade_point"],
                            "range": f"{r['min_percent']}-{r['max_percent']}%", "pass": bool(r["is_pass"])}
                           for r in scheme["rules"]]},
            "subjects": subjects, "decimals": _places(),
            "repeat_policy": current_app.config["CGPA_REPEAT_POLICY"]}
