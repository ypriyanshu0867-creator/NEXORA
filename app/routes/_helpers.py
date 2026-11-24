"""Small helpers shared by the portal routes."""
import datetime

from flask import g, request

from app.services import profile_service


def context():
    return profile_service.get_context(g.user)


def viewer():
    ctx = context()
    return {"role": ctx["role"], "department_id": ctx["department_id"],
            "semester_number": ctx["semester_number"], "class_id": ctx.get("class_id")}


def page_arg():
    return max(request.args.get("page", 1, type=int) or 1, 1)


def date_arg(name):
    try:
        return datetime.datetime.strptime(request.args.get(name, ""), "%Y-%m-%d")
    except ValueError:
        return None
