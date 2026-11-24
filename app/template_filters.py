"""Jinja filters shared by the portal templates."""
from decimal import ROUND_HALF_UP, Decimal


def money(value):
    """Exact two-decimal formatting for Decimal amounts (never float)."""
    if value is None:
        return "-"
    return f"{Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def gpa(value, places=2):
    if value is None:
        return "-"
    quantum = Decimal(1).scaleb(-places)
    return str(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def init_app(app):
    app.jinja_env.filters["money"] = money
    app.jinja_env.filters["gpa"] = gpa
