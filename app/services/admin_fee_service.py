
"""Admin fee management: structures, student records and transactions."""

from decimal import Decimal

from app.db import execute, query_all, query_one, transaction
from app.services import audit_service


def get_fee_summary():
    structures = query_one(
        "SELECT COUNT(*) AS structure_count, "
        "COALESCE(SUM(total_amount), 0) AS assessed "
        "FROM fee_structures"
    )

    records = query_one(
        "SELECT COUNT(*) AS record_count, "
        "COALESCE(SUM(amount_due), 0) AS total_due "
        "FROM student_fee_records"
    )

    payments = query_one(
        "SELECT "
        "COALESCE(SUM(CASE WHEN status='confirmed' THEN amount ELSE 0 END),0) AS confirmed, "
        "COALESCE(SUM(CASE WHEN status='pending' THEN amount ELSE 0 END),0) AS pending, "
        "COALESCE(SUM(CASE WHEN status='failed' THEN amount ELSE 0 END),0) AS failed "
        "FROM fee_transactions"
    )

    outstanding = query_one(
        "SELECT COALESCE(SUM("
        "CASE WHEN sfr.status='waived' THEN 0 "
        "ELSE GREATEST(sfr.amount_due - COALESCE(p.confirmed,0),0) END"
        "),0) AS outstanding "
        "FROM student_fee_records sfr "
        "LEFT JOIN ("
        "SELECT fee_record_id, SUM(amount) AS confirmed "
        "FROM fee_transactions WHERE status='confirmed' "
        "GROUP BY fee_record_id"
        ") p ON p.fee_record_id=sfr.id"
    )

    overdue = query_one(
        "SELECT COUNT(*) AS overdue_count "
        "FROM student_fee_records sfr "
        "LEFT JOIN ("
        "SELECT fee_record_id, SUM(amount) AS confirmed "
        "FROM fee_transactions WHERE status='confirmed' "
        "GROUP BY fee_record_id"
        ") p ON p.fee_record_id=sfr.id "
        "WHERE sfr.status <> 'waived' "
        "AND sfr.due_date < CURDATE() "
        "AND sfr.amount_due > COALESCE(p.confirmed,0)"
    )

    return {
        "structure_count": structures["structure_count"],
        "assessed": records["total_due"],
        "record_count": records["record_count"],
        "confirmed": payments["confirmed"],
        "pending": payments["pending"],
        "failed": payments["failed"],
        "outstanding": outstanding["outstanding"],
        "overdue_count": overdue["overdue_count"],
    }


def list_fee_structures():
    return query_all(
        "SELECT fs.id, fs.name, fs.department_id, d.name AS department_name, "
        "fs.academic_year_id, ay.label AS year_label, fs.total_amount, "
        "fs.due_date, fs.category, fs.semester_number "
        "FROM fee_structures fs "
        "LEFT JOIN departments d ON d.id=fs.department_id "
        "JOIN academic_years ay ON ay.id=fs.academic_year_id "
        "ORDER BY ay.start_date DESC, fs.name"
    )


def list_active_students():
    return query_all(
        "SELECT u.id, u.full_name, u.email, sp.roll_number, "
        "sp.department_id, d.name AS department_name "
        "FROM users u "
        "JOIN student_profiles sp ON sp.user_id=u.id "
        "LEFT JOIN departments d ON d.id=sp.department_id "
        "WHERE u.role='student' AND u.status='active' "
        "ORDER BY u.full_name"
    )


def list_student_fee_records(search="", status=""):
    conditions = []
    params = []

    if search:
        term = f"%{search.strip()[:100]}%"
        conditions.append(
            "(u.full_name LIKE %s OR u.email LIKE %s "
            "OR sp.roll_number LIKE %s OR fs.name LIKE %s)"
        )
        params.extend([term, term, term, term])

    if status in ("unpaid", "partial", "paid", "waived"):
        conditions.append("sfr.status=%s")
        params.append(status)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    return query_all(
        "SELECT sfr.id, sfr.student_id, sfr.fee_structure_id, "
        "sfr.amount_due, sfr.amount_paid, sfr.due_date, sfr.status, sfr.remarks, "
        "u.full_name AS student_name, u.email, sp.roll_number, "
        "d.name AS department_name, fs.name AS fee_name, "
        "COALESCE(p.confirmed,0) AS confirmed_paid "
        "FROM student_fee_records sfr "
        "JOIN users u ON u.id=sfr.student_id "
        "LEFT JOIN student_profiles sp ON sp.user_id=u.id "
        "LEFT JOIN departments d ON d.id=sp.department_id "
        "JOIN fee_structures fs ON fs.id=sfr.fee_structure_id "
        "LEFT JOIN ("
        "SELECT fee_record_id, SUM(amount) AS confirmed "
        "FROM fee_transactions WHERE status='confirmed' "
        "GROUP BY fee_record_id"
        ") p ON p.fee_record_id=sfr.id "
        + where +
        " ORDER BY sfr.due_date ASC, u.full_name",
        tuple(params)
    )


def list_transactions(status=""):
    conditions = []
    params = []

    if status in ("confirmed", "pending", "failed"):
        conditions.append("ft.status=%s")
        params.append(status)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    return query_all(
        "SELECT ft.id, ft.fee_record_id, ft.amount, ft.method, "
        "ft.reference_no, ft.paid_on, ft.note, ft.status, "
        "u.full_name AS student_name, sp.roll_number, "
        "fs.name AS fee_name, recorder.full_name AS recorded_by_name "
        "FROM fee_transactions ft "
        "JOIN student_fee_records sfr ON sfr.id=ft.fee_record_id "
        "JOIN users u ON u.id=sfr.student_id "
        "LEFT JOIN student_profiles sp ON sp.user_id=u.id "
        "JOIN fee_structures fs ON fs.id=sfr.fee_structure_id "
        "LEFT JOIN users recorder ON recorder.id=ft.recorded_by "
        + where +
        " ORDER BY ft.paid_on DESC, ft.id DESC",
        tuple(params)
    )


def create_fee_structure(data, admin_id, ip):
    with transaction():
        result = execute(
            "INSERT INTO fee_structures "
            "(name, department_id, academic_year_id, total_amount, "
            "due_date, category, semester_number) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                data["name"],
                data["department_id"],
                data["academic_year_id"],
                data["total_amount"],
                data["due_date"],
                data["category"],
                data["semester_number"],
            )
        )

        audit_service.log(
            "fee.structure.create",
            user_id=admin_id,
            entity_type="fee_structure",
            entity_id=result.lastrowid,
            details={
                "name": data["name"],
                "category": data["category"],
            },
            ip=ip
        )

    return result.lastrowid


def assign_fee(structure_id, student_id, admin_id, ip):
    with transaction():
        structure = query_one(
            "SELECT id, total_amount, due_date, department_id "
            "FROM fee_structures WHERE id=%s FOR UPDATE",
            (structure_id,)
        )

        student = query_one(
            "SELECT u.id, sp.department_id "
            "FROM users u JOIN student_profiles sp ON sp.user_id=u.id "
            "WHERE u.id=%s AND u.role='student' AND u.status='active'",
            (student_id,)
        )

        if not structure or not student:
            return False, "Select a valid fee structure and active student."

        if (
            structure["department_id"] is not None
            and structure["department_id"] != student["department_id"]
        ):
            return False, "This fee structure belongs to another department."

        result = execute(
            "INSERT INTO student_fee_records "
            "(student_id, fee_structure_id, amount_due, due_date, status) "
            "VALUES (%s,%s,%s,%s,'unpaid')",
            (
                student_id,
                structure_id,
                structure["total_amount"],
                structure["due_date"],
            )
        )

        audit_service.log(
            "fee.student.assign",
            user_id=admin_id,
            entity_type="student_fee_record",
            entity_id=result.lastrowid,
            details={
                "student_id": student_id,
                "structure_id": structure_id,
            },
            ip=ip
        )

    return True, "Fee assigned successfully."


def add_payment(data, admin_id, ip):
    with transaction():
        record = query_one(
            "SELECT id, amount_due, status FROM student_fee_records "
            "WHERE id=%s FOR UPDATE",
            (data["fee_record_id"],)
        )

        if not record:
            return False, "Fee record not found."

        if record["status"] == "waived":
            return False, "Cannot record payment against a waived fee."

        totals = query_one(
            "SELECT COALESCE(SUM(amount),0) AS reserved "
            "FROM fee_transactions "
            "WHERE fee_record_id=%s AND status IN ('confirmed','pending')",
            (data["fee_record_id"],)
        )

        remaining = (
            Decimal(record["amount_due"]) - Decimal(totals["reserved"])
        )

        if data["amount"] <= 0 or data["amount"] > remaining:
            return False, "Payment amount exceeds the remaining balance."

        result = execute(
            "INSERT INTO fee_transactions "
            "(fee_record_id, amount, method, reference_no, paid_on, "
            "note, recorded_by, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                data["fee_record_id"],
                data["amount"],
                data["method"],
                data["reference_no"] or None,
                data["paid_on"],
                data["note"] or None,
                admin_id,
                data["status"],
            )
        )

        if data["status"] == "confirmed":
            _sync_fee_record(data["fee_record_id"])

        audit_service.log(
            "fee.payment.create",
            user_id=admin_id,
            entity_type="fee_transaction",
            entity_id=result.lastrowid,
            details={
                "fee_record_id": data["fee_record_id"],
                "amount": str(data["amount"]),
                "status": data["status"],
                "method": data["method"],
            },
            ip=ip
        )

    return True, "Payment entry recorded successfully."


def _sync_fee_record(record_id):
    totals = query_one(
        "SELECT COALESCE(SUM(amount),0) AS confirmed "
        "FROM fee_transactions "
        "WHERE fee_record_id=%s AND status='confirmed'",
        (record_id,)
    )

    record = query_one(
        "SELECT amount_due, status FROM student_fee_records WHERE id=%s",
        (record_id,)
    )

    if not record or record["status"] == "waived":
        return

    paid = min(Decimal(totals["confirmed"]), Decimal(record["amount_due"]))
    due = Decimal(record["amount_due"])

    status = "paid" if paid >= due else (
        "partial" if paid > 0 else "unpaid"
    )

    execute(
        "UPDATE student_fee_records SET amount_paid=%s, status=%s WHERE id=%s",
        (paid, status, record_id)
    )


def review_payment(transaction_id, new_status, admin_id, ip):
    if new_status not in ("confirmed", "failed"):
        raise ValueError("Invalid payment status.")

    with transaction():
        payment = query_one(
            "SELECT id, fee_record_id, amount, status "
            "FROM fee_transactions WHERE id=%s FOR UPDATE",
            (transaction_id,)
        )

        if not payment or payment["status"] != "pending":
            return False, "Payment not found or already reviewed."

        if new_status == "confirmed":
            record = query_one(
                "SELECT id, amount_due, status FROM student_fee_records "
                "WHERE id=%s FOR UPDATE",
                (payment["fee_record_id"],)
            )

            if not record or record["status"] == "waived":
                return False, "Cannot confirm payment for this fee record."

            confirmed = query_one(
                "SELECT COALESCE(SUM(amount),0) AS total "
                "FROM fee_transactions "
                "WHERE fee_record_id=%s AND status='confirmed'",
                (payment["fee_record_id"],)
            )

            if (
                Decimal(confirmed["total"]) + Decimal(payment["amount"])
                > Decimal(record["amount_due"])
            ):
                return False, "Confirmation would exceed the fee balance."

        execute(
            "UPDATE fee_transactions SET status=%s "
            "WHERE id=%s AND status='pending'",
            (new_status, transaction_id)
        )

        if new_status == "confirmed":
            _sync_fee_record(payment["fee_record_id"])

        audit_service.log(
            "fee.payment." + new_status,
            user_id=admin_id,
            entity_type="fee_transaction",
            entity_id=transaction_id,
            details={
                "fee_record_id": payment["fee_record_id"],
            },
            ip=ip
        )

    return True, "Payment marked as " + new_status + "."


def waive_fee(record_id, remarks, admin_id, ip):
    if not remarks or len(remarks) > 255:
        return False, "A waiver reason is required (maximum 255 characters)."

    with transaction():
        record = query_one(
            "SELECT id, status FROM student_fee_records "
            "WHERE id=%s FOR UPDATE",
            (record_id,)
        )

        if not record:
            return False, "Fee record not found."

        if record["status"] == "waived":
            return False, "This fee is already waived."

        payments = query_one(
            "SELECT "
            "COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) "
            "AS pending, "
            "COALESCE(SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END),0) "
            "AS confirmed "
            "FROM fee_transactions WHERE fee_record_id=%s",
            (record_id,)
        )

        if payments["pending"]:
            return False, "Review pending payments before waiving this fee."

        if payments["confirmed"]:
            return False, "A fee with confirmed payments cannot be waived."

        execute(
            "UPDATE student_fee_records SET status='waived', remarks=%s "
            "WHERE id=%s",
            (remarks, record_id)
        )

        audit_service.log(
            "fee.record.waive",
            user_id=admin_id,
            entity_type="student_fee_record",
            entity_id=record_id,
            details={"reason": remarks},
            ip=ip
        )

    return True, "Fee waived successfully."


def list_academic_years():
    return query_all(
        "SELECT id, label, is_current "
        "FROM academic_years "
        "ORDER BY start_date DESC"
    )


def list_departments():
    return query_all(
        "SELECT id, name FROM departments ORDER BY name"
    )
