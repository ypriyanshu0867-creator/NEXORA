"""Fee tracking (records only; no payment gateway, no card or banking data).

Outstanding balance = amount assessed - CONFIRMED payments, computed from
the transactions themselves, not from the cached student_fee_records.amount_paid
column. Pending and failed transactions are listed but never counted.
A waived record has no outstanding balance. All money is Decimal.
"""
from decimal import Decimal

from app import clock
from app.db import query_all

ZERO = Decimal("0.00")


def fee_status(amount_due, confirmed, due_date, today, waived):
    """Pure rule -> (status label, outstanding Decimal)."""
    due, paid = Decimal(amount_due), Decimal(confirmed)
    if waived:
        return "Waived", ZERO
    outstanding = max(due - paid, ZERO)
    if outstanding == 0:
        return "Paid", ZERO
    if due_date < today:
        return "Overdue", outstanding
    return ("Partially paid" if paid > 0 else "Pending"), outstanding


def fee_overview(student_id):
    today = clock.now().date()
    records = query_all(
        "SELECT sfr.id, sfr.amount_due, sfr.due_date, sfr.status AS stored_status, sfr.remarks, "
        "       fs.name AS fee_name, fs.category, fs.semester_number, ay.label AS year_label "
        "FROM student_fee_records sfr "
        "JOIN fee_structures fs ON fs.id = sfr.fee_structure_id "
        "JOIN academic_years ay ON ay.id = fs.academic_year_id "
        "WHERE sfr.student_id = %s ORDER BY sfr.due_date DESC, sfr.id DESC", (student_id,))
    transactions = query_all(
        "SELECT ft.id, ft.fee_record_id, ft.amount, ft.method, ft.reference_no, ft.paid_on, ft.status, ft.note "
        "FROM fee_transactions ft JOIN student_fee_records sfr ON sfr.id = ft.fee_record_id "
        "WHERE sfr.student_id = %s ORDER BY ft.paid_on DESC, ft.id DESC", (student_id,))
    by_record = {}
    for tx in transactions:
        by_record.setdefault(tx["fee_record_id"], []).append(tx)

    totals = {"assessed": ZERO, "confirmed": ZERO, "outstanding": ZERO, "awaiting_confirmation": ZERO}
    for rec in records:
        txs = by_record.get(rec["id"], [])
        confirmed = sum((Decimal(t["amount"]) for t in txs if t["status"] == "confirmed"), ZERO)
        rec["confirmed_paid"] = confirmed
        rec["awaiting_confirmation"] = sum((Decimal(t["amount"]) for t in txs if t["status"] == "pending"), ZERO)
        rec["status"], rec["outstanding"] = fee_status(
            rec["amount_due"], confirmed, rec["due_date"], today, rec["stored_status"] == "waived")
        rec["transactions"] = txs
        totals["assessed"] += Decimal(rec["amount_due"])
        totals["confirmed"] += confirmed
        totals["outstanding"] += rec["outstanding"]
        totals["awaiting_confirmation"] += rec["awaiting_confirmation"]
    return {"records": records, "totals": totals,
            "has_overdue": any(r["status"] == "Overdue" for r in records)}
