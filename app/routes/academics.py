from flask import Blueprint, g, render_template

from app.routes._helpers import context
from app.security import role_required
from app.services import academic_service

bp = Blueprint("academics", __name__, url_prefix="/academics")


@bp.route("/results")
@role_required("student")
def results():
    ctx = context()
    return render_template("student/results.html",
                           overview=academic_service.results_overview(g.user["id"], ctx["semester_id"]))


@bp.route("/calculator")
@role_required("student")
def calculator():
    return render_template("student/calculator.html", data=academic_service.calculator_data(context()))
