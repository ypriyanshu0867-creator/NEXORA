"""Student-facing library catalogue and personal borrowing records.

Read-only: students cannot change inventory, issues, returns or fines.
Issue/return operations belong to the Phase 5 staff interfaces; they must
run in a transaction and never let available_copies go below 0 (the schema's
CHECK constraint already guarantees available <= total).
"""
from decimal import Decimal

from flask import current_app

from app import clock
from app.db import query_all, query_one

ZERO = Decimal("0.00")


def _like(text):
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def categories():
    return [r["category"] for r in query_all("SELECT DISTINCT category FROM library_books ORDER BY category")]


def search_books(q="", category="", only_available=False, page=1, page_size=None):
    page_size = page_size or current_app.config["PAGE_SIZE"]
    where, params = [], []
    q = (q or "").strip()[:100]
    if q:
        isbn_digits = "".join(ch for ch in q if ch.isalnum())
        where.append("(b.title LIKE %s OR b.author LIKE %s OR REPLACE(b.isbn, '-', '') LIKE %s)")
        params += [_like(q), _like(q), _like(isbn_digits or q)]
    if category:
        where.append("b.category = %s")
        params.append(category)
    if only_available:
        where.append("b.available_copies > 0")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = query_one("SELECT COUNT(*) AS c FROM library_books b" + clause, tuple(params))["c"]
    pages = max(1, -(-total // page_size))
    page = min(max(int(page or 1), 1), pages)
    items = query_all(
        "SELECT b.id, b.title, b.author, b.isbn, b.category, b.publisher, b.publication_year, "
        "       b.shelf_location, b.total_copies, b.available_copies "
        "FROM library_books b" + clause + " ORDER BY b.title, b.id LIMIT %s OFFSET %s",
        tuple(params) + (page_size, (page - 1) * page_size))
    for b in items:
        b["availability"] = "Available" if b["available_copies"] > 0 else "All copies issued"
    return {"items": items, "total": total, "page": page, "pages": pages, "page_size": page_size}


def get_book(book_id):
    book = query_one("SELECT id, title, author, isbn, category, publisher, publication_year, shelf_location, "
                     "total_copies, available_copies FROM library_books WHERE id = %s", (book_id,))
    if book:
        book["availability"] = "Available" if book["available_copies"] > 0 else "All copies issued"
    return book


def classify_issue(issue, today, fine_per_day):
    """Pure rule: status, days overdue and fine for one issue row."""
    stored = Decimal(issue["fine_amount"])
    if issue["returned_on"] is not None:
        late = max((issue["returned_on"] - issue["due_on"]).days, 0)
        return {"status": "Returned", "days_overdue": late, "fine": stored}
    overdue = max((today - issue["due_on"]).days, 0)
    fine = Decimal(overdue) * fine_per_day if fine_per_day > 0 else stored
    return {"status": "Overdue" if overdue else "Issued", "days_overdue": overdue, "fine": max(fine, stored)}


def student_issues(student_id):
    """ONLY this student's issue history (the id comes from the login)."""
    today = clock.now().date()
    rate = Decimal(current_app.config["LIBRARY_FINE_PER_DAY"])
    rows = query_all(
        "SELECT li.id, li.book_id, b.title, b.author, li.issued_on, li.due_on, li.returned_on, li.fine_amount "
        "FROM library_issues li JOIN library_books b ON b.id = li.book_id "
        "WHERE li.student_id = %s ORDER BY (li.returned_on IS NULL) DESC, li.due_on DESC, li.id DESC",
        (student_id,))
    for r in rows:
        r.update(classify_issue(r, today, rate))
    current = [r for r in rows if r["status"] in ("Issued", "Overdue")]
    return {"current": current, "returned": [r for r in rows if r["status"] == "Returned"],
            "overdue_count": sum(1 for r in current if r["status"] == "Overdue"),
            "fines_configured": rate > 0, "total_fines": sum((Decimal(r["fine"]) for r in rows), ZERO)}
