from flask import Blueprint, abort, render_template, request

from app.routes._helpers import date_arg, page_arg, viewer
from app.security import login_required
from app.services import file_service, notice_service

bp = Blueprint("notices", __name__, url_prefix="/notices")


@bp.route("/")
@login_required
def index():
    result = notice_service.list_notices(
        viewer(), request.args.get("q", ""), request.args.get("category", ""),
        date_arg("from"), date_arg("to"), request.args.get("expired") == "1", page_arg())
    return render_template("student/notices.html", result=result, categories=notice_service.CATEGORIES,
                           q=request.args.get("q", ""), category=request.args.get("category", ""),
                           date_from=request.args.get("from", ""), date_to=request.args.get("to", ""),
                           include_expired=request.args.get("expired") == "1")


@bp.route("/<int:notice_id>")
@login_required
def detail(notice_id):
    notice = notice_service.get_notice(viewer(), notice_id)
    if notice is None:
        abort(404)                                       # unpublished or not addressed to you
    return render_template("student/notice_detail.html", notice=notice)


@bp.route("/<int:notice_id>/attachment")
@login_required
def attachment(notice_id):
    notice = notice_service.get_notice(viewer(), notice_id)
    if notice is None or not notice["has_attachment"]:
        abort(404)
    return file_service.send_pdf("notices", notice["attachment_stored_name"],
                                 notice["attachment_original_name"], f"notice-{notice['id']}.pdf")
