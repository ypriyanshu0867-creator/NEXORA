from flask import Blueprint, abort, g, render_template, request

from app.routes._helpers import page_arg
from app.security import login_required, role_required
from app.services import library_service

bp = Blueprint("library", __name__, url_prefix="/library")


@bp.route("/")
@login_required
def catalog():
    result = library_service.search_books(request.args.get("q", ""), request.args.get("category", ""),
                                          request.args.get("available") == "1", page_arg())
    return render_template("student/library_catalog.html", result=result,
                           categories=library_service.categories(), q=request.args.get("q", ""),
                           category=request.args.get("category", ""),
                           only_available=request.args.get("available") == "1")


@bp.route("/book/<int:book_id>")
@login_required
def book(book_id):
    found = library_service.get_book(book_id)
    if found is None:
        abort(404)
    return render_template("student/library_book.html", book=found)


@bp.route("/my-books")
@role_required("student")
def my_books():
    return render_template("student/library_my_books.html", issues=library_service.student_issues(g.user["id"]))


@bp.route("/manage", methods=["GET", "POST"], endpoint="manage")
@role_required("admin")
def manage():
    from datetime import date
    from decimal import Decimal, InvalidOperation

    from flask import flash, redirect, url_for
    from app.db import IntegrityError
    from app.services import admin_library_service

    if request.method == "POST":
        action = request.form.get("action", "add")

        if action == "add":
            redirect_url = url_for("library.manage")

        elif action == "edit":
            redirect_url = url_for("library.manage")

        else:
            flash("Invalid library action.", "danger")
            return redirect(url_for("library.manage"))

        try:
            total = int(request.form.get("total_copies", ""))
            if total < 1 or total > 65535:
                raise ValueError

            year_raw = request.form.get("publication_year", "").strip()
            year = int(year_raw) if year_raw else None
            if year is not None and not (1000 <= year <= date.today().year + 5):
                raise ValueError

            data = {
                "isbn": request.form.get("isbn", "").strip(),
                "title": request.form.get("title", "").strip(),
                "author": request.form.get("author", "").strip(),
                "category": request.form.get("category", "").strip(),
                "shelf_location": request.form.get("shelf_location", "").strip(),
                "total_copies": total,
                "publisher": request.form.get("publisher", "").strip(),
                "publication_year": year,
            }

            if not data["title"] or len(data["title"]) > 200:
                raise ValueError
            if not data["author"] or len(data["author"]) > 150:
                raise ValueError
            if not data["category"] or len(data["category"]) > 80:
                raise ValueError
            if len(data["isbn"]) > 20 or len(data["shelf_location"]) > 40:
                raise ValueError
            if len(data["publisher"]) > 120:
                raise ValueError

            if action == "add":
                admin_library_service.add_book(
                    data, g.user["id"], request.remote_addr
                )
                flash("Book added successfully.", "success")

            else:
                book_id = int(request.form.get("book_id", ""))
                updated, error = admin_library_service.update_book(
                    book_id, data, g.user["id"], request.remote_addr
                )
                if not updated:
                    flash(error, "danger")
                else:
                    flash("Book updated successfully.", "success")

        except (ValueError, TypeError, InvalidOperation):
            flash("Please enter valid book details and copy count.", "danger")
        except IntegrityError:
            flash("This ISBN already exists. Please use a unique ISBN.", "danger")

        return redirect(redirect_url)

    search = request.args.get("q", "").strip()[:100]
    books = admin_library_service.list_books(search)
    issues = admin_library_service.list_issues()
    summary = admin_library_service.get_library_summary()

    return render_template(
        "admin/library.html",
        books=books,
        issues=issues,
        summary=summary,
        search=search,
    )

