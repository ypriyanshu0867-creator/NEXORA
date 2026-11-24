
"""Admin-only library catalogue and inventory operations."""

from app.db import execute, query_all, query_one, transaction
from app.services import audit_service


def list_books(search=""):
    search = (search or "").strip()[:100]

    sql = (
        "SELECT b.id, b.isbn, b.title, b.author, b.category, "
        "b.shelf_location, b.total_copies, b.available_copies, "
        "b.publisher, b.publication_year, "
        "(b.total_copies - b.available_copies) AS issued_copies "
        "FROM library_books b "
    )
    params = ()

    if search:
        pattern = f"%{search}%"
        sql += (
            "WHERE b.title LIKE %s OR b.author LIKE %s "
            "OR b.isbn LIKE %s OR b.category LIKE %s "
        )
        params = (pattern, pattern, pattern, pattern)

    sql += "ORDER BY b.title, b.id"
    return query_all(sql, params)


def list_categories():
    rows = query_all(
        "SELECT DISTINCT category FROM library_books ORDER BY category"
    )
    return [row["category"] for row in rows]


def list_issues(status="all"):
    sql = (
        "SELECT li.id, li.book_id, li.student_id, li.issued_on, "
        "li.due_on, li.returned_on, li.fine_amount, li.issued_by, "
        "b.title AS book_title, b.isbn, "
        "u.full_name AS student_name, u.email AS student_email, "
        "sp.roll_number, issuer.full_name AS issued_by_name "
        "FROM library_issues li "
        "JOIN library_books b ON b.id = li.book_id "
        "JOIN users u ON u.id = li.student_id "
        "LEFT JOIN student_profiles sp ON sp.user_id = u.id "
        "LEFT JOIN users issuer ON issuer.id = li.issued_by "
    )

    if status == "active":
        sql += "WHERE li.returned_on IS NULL "
    elif status == "returned":
        sql += "WHERE li.returned_on IS NOT NULL "

    sql += "ORDER BY li.returned_on IS NULL DESC, li.due_on ASC, li.id DESC"
    return query_all(sql)


def add_book(data, admin_id, ip):
    with transaction():
        result = execute(
            "INSERT INTO library_books "
            "(isbn, title, author, category, shelf_location, "
            "total_copies, available_copies, publisher, publication_year) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                data["isbn"] or None,
                data["title"],
                data["author"],
                data["category"],
                data["shelf_location"] or None,
                data["total_copies"],
                data["total_copies"],
                data["publisher"] or None,
                data["publication_year"],
            ),
        )

        audit_service.log(
            "library.book.create",
            user_id=admin_id,
            entity_type="library_book",
            entity_id=result.lastrowid,
            details={
                "title": data["title"],
                "isbn": data["isbn"] or None,
                "total_copies": data["total_copies"],
            },
            ip=ip,
        )

    return result.lastrowid


def update_book(book_id, data, admin_id, ip):
    with transaction():
        current = query_one(
            "SELECT id, total_copies, available_copies "
            "FROM library_books WHERE id = %s FOR UPDATE",
            (book_id,),
        )

        if current is None:
            return False, "Book not found."

        issued_copies = current["total_copies"] - current["available_copies"]
        new_total = data["total_copies"]

        if new_total < issued_copies:
            return False, (
                f"Cannot set total copies below {issued_copies}; "
                "some copies are currently issued."
            )

        new_available = new_total - issued_copies

        execute(
            "UPDATE library_books SET isbn = %s, title = %s, author = %s, "
            "category = %s, shelf_location = %s, total_copies = %s, "
            "available_copies = %s, publisher = %s, publication_year = %s "
            "WHERE id = %s",
            (
                data["isbn"] or None,
                data["title"],
                data["author"],
                data["category"],
                data["shelf_location"] or None,
                new_total,
                new_available,
                data["publisher"] or None,
                data["publication_year"],
                book_id,
            ),
        )

        audit_service.log(
            "library.book.update",
            user_id=admin_id,
            entity_type="library_book",
            entity_id=book_id,
            details={
                "title": data["title"],
                "total_copies": new_total,
                "available_copies": new_available,
            },
            ip=ip,
        )

    return True, None


def get_library_summary():
    books = query_one(
        "SELECT COUNT(*) AS titles, "
        "COALESCE(SUM(total_copies), 0) AS total_copies, "
        "COALESCE(SUM(available_copies), 0) AS available_copies "
        "FROM library_books"
    )

    issues = query_one(
        "SELECT COUNT(*) AS active_issues, "
        "COALESCE(SUM(CASE WHEN due_on < CURDATE() "
        "THEN 1 ELSE 0 END), 0) AS overdue_issues "
        "FROM library_issues WHERE returned_on IS NULL"
    )

    fines = query_one(
        "SELECT COALESCE(SUM(fine_amount), 0) AS recorded_fines "
        "FROM library_issues"
    )

    return {
        "titles": books["titles"],
        "total_copies": books["total_copies"],
        "available_copies": books["available_copies"],
        "issued_copies": books["total_copies"] - books["available_copies"],
        "active_issues": issues["active_issues"],
        "overdue_issues": issues["overdue_issues"],
        "recorded_fines": fines["recorded_fines"],
    }
