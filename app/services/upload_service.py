"""Validated PDF uploads (learning materials, notice attachments).

* only .pdf names are accepted and the content must start with %PDF-
* size is capped (MATERIAL_MAX_MB); the stored name is a random uuid, never the uploaded name
* files go to instance/uploads/<subdir>/ (outside static/, served only through
  file_service.send_pdf), written to a temp name then renamed, and never executed
* PDFs can contain scripts; they are always sent as downloads, never rendered by the site
"""
import hashlib
import os
import uuid

from flask import current_app
from werkzeug.utils import secure_filename

from app.services.errors import ServiceError
from app.services.file_service import upload_root


def save_pdf(file_storage, subdir, field="file"):
    """Validate and store an upload. Returns {stored_name, original_name, size, sha256}."""
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise ServiceError("file_required", "Choose a PDF file to upload.", 422,
                           fields={field: "Choose a PDF file."})

    if not file_storage.filename.lower().endswith(".pdf"):
        raise ServiceError("invalid_file_type", "Only PDF files are allowed.", 422,
                           fields={field: "Only .pdf files."})

    limit = current_app.config["MATERIAL_MAX_BYTES"]
    data = file_storage.stream.read(limit + 1)

    if not data:
        raise ServiceError("file_required", "Choose a PDF file to upload.", 422,
                           fields={field: "Choose a PDF file."})

    if len(data) > limit:
        raise ServiceError("file_too_large", f"The file is larger than {limit // (1024 * 1024)} MB.", 413,
                           fields={field: "File too large."})

    if len(data) < 8 or not data.startswith(b"%PDF-"):
        raise ServiceError("invalid_pdf", "That file is not a valid PDF.", 422,
                           fields={field: "Not a valid PDF."})

    stored = uuid.uuid4().hex + ".pdf"
    folder = upload_root() / subdir
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / (stored + ".part")
    temp.write_bytes(data)
    os.replace(temp, folder / stored)

    return {
        "stored_name": stored,
        "original_name": (secure_filename(file_storage.filename) or "upload.pdf")[:255],
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest()
    }


def remove_file(subdir, stored_name):
    """Best-effort cleanup (used if the database write fails or a file is replaced)."""
    try:
        path = (upload_root() / subdir / stored_name)
        if path.parent == upload_root() / subdir and path.is_file():
            path.unlink()
    except OSError:
        current_app.logger.exception("Could not remove uploaded file")
