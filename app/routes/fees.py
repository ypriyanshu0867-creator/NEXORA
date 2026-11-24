
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint, flash, g, redirect,
    render_template, request, url_for
)

from app.db import IntegrityError
from app.security import role_required
from app.services import fee_service, admin_fee_service


bp = Blueprint("fees", __name__, url_prefix="/fees")


# ---------------------------------------------------------
# Student Fees
# ---------------------------------------------------------

@bp.route("/")
@role_required("student")
def my_fees():
    return render_template(
        "student/fees.html",
        overview=fee_service.fee_overview(g.user["id"])
    )


# ---------------------------------------------------------
# Form Validation Helpers
# ---------------------------------------------------------

def _required_int(field):
    try:
        value = int(request.form.get(field, ""))
    except (TypeError, ValueError):
        raise ValueError("Please select valid options in all required fields.")

    if value <= 0:
        raise ValueError("Invalid selection.")

    return value


def _valid_date(field):
    value = request.form.get(field, "").strip()

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("Enter a valid date.")


def _valid_amount(field):
    value = request.form.get(field, "").strip()

    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter a valid payment amount.")

    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    if amount > Decimal("99999999.99"):
        raise ValueError("Amount exceeds the allowed limit.")

    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Amount can have a maximum of two decimal places.")

    return amount


def _structure_form_data():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()

    if not name or len(name) > 120:
        raise ValueError("Fee name is required (maximum 120 characters).")

    if not category or len(category) > 40:
        raise ValueError("Enter a valid fee category.")

    department_raw = request.form.get("department_id", "").strip()
    semester_raw = request.form.get("semester_number", "").strip()

    try:
        department_id = int(department_raw) if department_raw else None
        semester_number = int(semester_raw) if semester_raw else None
    except ValueError:
        raise ValueError("Select a valid department or semester.")

    if department_id is not None and department_id <= 0:
        raise ValueError("Select a valid department.")

    if semester_number is not None and not 1 <= semester_number <= 8:
        raise ValueError("Semester must be between 1 and 8.")

    return {
        "name": name,
        "category": category,
        "department_id": department_id,
        "academic_year_id": _required_int("academic_year_id"),
        "total_amount": _valid_amount("total_amount"),
        "due_date": _valid_date("due_date"),
        "semester_number": semester_number,
    }


def _payment_form_data():
    method = request.form.get("method", "").strip()
    status = request.form.get("status", "").strip()

    if method not in ("cash", "bank_transfer", "cheque", "other"):
        raise ValueError("Select a valid payment method.")

    if status not in ("pending", "confirmed"):
        raise ValueError("Select a valid initial payment status.")

    reference = request.form.get("reference_no", "").strip()
    note = request.form.get("note", "").strip()

    if len(reference) > 60:
        raise ValueError("Reference number cannot exceed 60 characters.")

    if len(note) > 255:
        raise ValueError("Payment note cannot exceed 255 characters.")

    return {
        "fee_record_id": _required_int("fee_record_id"),
        "amount": _valid_amount("amount"),
        "method": method,
        "reference_no": reference,
        "paid_on": _valid_date("paid_on"),
        "note": note,
        "status": status,
    }


# ---------------------------------------------------------
# Admin Fees Dashboard
# ---------------------------------------------------------

@bp.route("/manage", methods=["GET", "POST"])
@role_required("admin")
def manage():

    if request.method == "POST":
        action = request.form.get("action", "").strip()

        try:

            # Create Fee Structure
            if action == "create_structure":
                data = _structure_form_data()

                admin_fee_service.create_fee_structure(
                    data,
                    g.user["id"],
                    request.remote_addr
                )

                flash("Fee structure created successfully.", "success")

            # Assign Fee to Student
            elif action == "assign_fee":
                structure_id = _required_int("structure_id")
                student_id = _required_int("student_id")

                success, message = admin_fee_service.assign_fee(
                    structure_id,
                    student_id,
                    g.user["id"],
                    request.remote_addr
                )

                flash(
                    message,
                    "success" if success else "warning"
                )

            # Record Payment
            elif action == "add_payment":
                data = _payment_form_data()

                success, message = admin_fee_service.add_payment(
                    data,
                    g.user["id"],
                    request.remote_addr
                )

                flash(
                    message,
                    "success" if success else "warning"
                )

            # Confirm or Reject Payment
            elif action == "review_payment":
                transaction_id = _required_int("transaction_id")
                new_status = request.form.get("new_status", "").strip()

                if new_status not in ("confirmed", "failed"):
                    raise ValueError("Invalid payment review action.")

                success, message = admin_fee_service.review_payment(
                    transaction_id,
                    new_status,
                    g.user["id"],
                    request.remote_addr
                )

                flash(
                    message,
                    "success" if success else "warning"
                )

            # Waive Student Fee
            elif action == "waive_fee":
                record_id = _required_int("record_id")
                remarks = request.form.get("remarks", "").strip()

                success, message = admin_fee_service.waive_fee(
                    record_id,
                    remarks,
                    g.user["id"],
                    request.remote_addr
                )

                flash(
                    message,
                    "success" if success else "warning"
                )

            else:
                flash("Invalid fee management action.", "danger")

        except ValueError as exc:
            flash(str(exc), "danger")

        except IntegrityError:
            flash(
                "This entry already exists or violates a database constraint.",
                "warning"
            )

        return redirect(url_for("fees.manage"))

    # Dashboard GET Request
    search = request.args.get("q", "").strip()[:100]
    status = request.args.get("status", "").strip()

    return render_template(
        "admin/fees.html",
        summary=admin_fee_service.get_fee_summary(),
        structures=admin_fee_service.list_fee_structures(),
        students=admin_fee_service.list_active_students(),
        records=admin_fee_service.list_student_fee_records(
            search, status
        ),
        transactions=admin_fee_service.list_transactions(),
        academic_years=admin_fee_service.list_academic_years(),
        departments=admin_fee_service.list_departments(),
        search=search,
        selected_status=status,
        today=date.today().isoformat(),
    )

