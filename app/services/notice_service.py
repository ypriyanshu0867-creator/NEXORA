"""Notice board. A notice is VISIBLE to a viewer only if all of these hold:
 * is_active = 1 (unpublished/withdrawn notices are never shown)
 * its publish time (published_at, else created_at) is not in the future
 * its audience includes the viewer's role
 * department matches (NULL = every department) and, for students, the target
   semester matches the student's current semester (NULL = every semester)
Expired notices (expires_at passed) are hidden by default, and shown only
when the viewer asks to include expired ones. Creating/editing notices is a
Phase 5 staff feature; nothing here writes.
"""
from flask import current_app

from app import clock
from app.db import query_all, query_one

CATEGORIES = [("general", "General"), ("exam", "Examination"), ("timetable", "Timetable"),
              ("holiday", "Holiday"), ("event", "Event"), ("assignment", "Assignment deadline"),
              ("result", "Academic results"), ("fee", "Fee deadline"), ("library", "Library"),
              ("material", "Learning material")]
CATEGORY_KEYS = {k for k, _ in CATEGORIES}
PUBLISHED = "COALESCE(n.published_at, n.created_at)"


def _audience_clause(viewer, now, include_expired):
    parts = ["n.is_active = 1", f"{PUBLISHED} <= %s"]
    params = [now]
    role = viewer["role"]
    if role == "student":
        parts += ["n.audience IN ('all', 'student')",
                  "(n.department_id IS NULL OR n.department_id = %s)",
                  "(n.semester_number IS NULL OR n.semester_number = %s)",
                  "(n.class_id IS NULL OR n.class_id = %s)"]
        params += [viewer["department_id"], viewer.get("semester_number") or 0, viewer.get("class_id") or 0]
    elif role == "teacher":
        parts += ["n.audience IN ('all', 'teacher')", "(n.department_id IS NULL OR n.department_id = %s)"]
        params.append(viewer["department_id"])
    if not include_expired:
        parts.append("(n.expires_at IS NULL OR n.expires_at > %s)")
        params.append(now)
    return " AND ".join(parts), params


def list_notices(viewer, q="", category="", date_from=None, date_to=None, include_expired=False,
                 page=1, page_size=None):
    page_size = page_size or current_app.config["PAGE_SIZE"]
    now = clock.now()
    clause, params = _audience_clause(viewer, now, include_expired)
    q = (q or "").strip()[:100]
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clause += " AND (n.title LIKE %s OR n.body LIKE %s)"
        params += [like, like]
    if category in CATEGORY_KEYS:
        clause += " AND n.category = %s"
        params.append(category)
    if date_from:
        clause += f" AND {PUBLISHED} >= %s"
        params.append(date_from)
    if date_to:
        clause += f" AND {PUBLISHED} < %s"
        params.append(date_to + _one_day())
    total = query_one("SELECT COUNT(*) AS c FROM notices n WHERE " + clause, tuple(params))["c"]
    pages = max(1, -(-total // page_size))
    page = min(max(int(page or 1), 1), pages)
    rows = query_all(
        f"SELECT n.id, n.title, n.category, n.is_pinned, {PUBLISHED} AS published, n.expires_at, n.deadline_at, "
        "       (n.attachment_stored_name IS NOT NULL) AS has_attachment, LEFT(n.body, 220) AS excerpt "
        "FROM notices n WHERE " + clause + " ORDER BY n.is_pinned DESC, published DESC, n.id DESC LIMIT %s OFFSET %s",
        tuple(params) + (page_size, (page - 1) * page_size))
    for r in rows:
        r["expired"] = r["expires_at"] is not None and r["expires_at"] <= now
    return {"items": rows, "total": total, "page": page, "pages": pages}


def _one_day():
    import datetime
    return datetime.timedelta(days=1)


def get_notice(viewer, notice_id):
    """One notice, only if visible to this viewer (expired ones stay reachable by direct link)."""
    now = clock.now()
    clause, params = _audience_clause(viewer, now, include_expired=True)
    row = query_one(
        f"SELECT n.id, n.title, n.body, n.category, n.is_pinned, {PUBLISHED} AS published, n.expires_at, "
        "       n.deadline_at, n.attachment_stored_name, n.attachment_original_name "
        "FROM notices n WHERE n.id = %s AND " + clause, (notice_id, *params))
    if row:
        row["expired"] = row["expires_at"] is not None and row["expires_at"] <= now
        row["has_attachment"] = row["attachment_stored_name"] is not None
    return row
