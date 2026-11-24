"""Safe serving of stored files (PDFs).

The browser never supplies a path. It supplies a database id; the stored
file name comes from the database, is re-validated here against a strict
pattern, resolved inside the configured upload directory, and its content
is checked to really be a PDF. The original upload name is used only as a
sanitised download name.
"""
import re
from pathlib import Path

from flask import current_app, send_file
from werkzeug.utils import secure_filename

from app.services.errors import ServiceError

STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}\.pdf$")        # uuid4().hex + ".pdf"
ALLOWED_EXTENSIONS = {".pdf"}


def upload_root():
    return Path(current_app.config["UPLOAD_FOLDER"]).resolve()


def resolve_stored_file(subdir, stored_name):
    """Return the real Path of a stored file or raise a 404 ServiceError."""
    missing = ServiceError("file_not_found", "This file is not available. Please contact your teacher or "
                           "the administrator.", 404)
    if not isinstance(stored_name, str) or not STORED_NAME_RE.match(stored_name):
        raise missing                                       # rejects ../, slashes, absolute paths, etc.
    base = (upload_root() / subdir).resolve()
    path = (base / stored_name).resolve()
    if base not in path.parents:                            # blocks symlinks/escapes
        raise missing
    if not path.is_file():
        raise missing
    try:
        with open(path, "rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise missing                               # content is not a PDF: never serve it
    except OSError:
        raise missing from None
    return path


def download_name(original_name, fallback):
    name = secure_filename(original_name or "") or fallback
    return name if name.lower().endswith(".pdf") else name + ".pdf"


def send_pdf(subdir, stored_name, original_name, fallback):
    path = resolve_stored_file(subdir, stored_name)
    return send_file(path, mimetype="application/pdf", as_attachment=True,
                     download_name=download_name(original_name, fallback), max_age=0)
