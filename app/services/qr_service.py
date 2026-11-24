
"""QR token creation, encryption, parsing and rendering.

Policy
* QR payload is NXR1.<token>, containing a cryptographically random token.
* Only SHA-256(token) and an encrypted copy are stored in the database.
* The encrypted copy allows the same QR to be recovered after a page reload.
* Raw tokens are never stored in plaintext.
* QR tokens remain multi-use until expiry so multiple students can scan
  the same classroom QR.
"""
import base64
import hashlib
import io
import re
import secrets
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from app.db import execute
from app.services.errors import ServiceError

PREFIX = "NXR1."
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def new_token():
    """Generate a cryptographically secure 256-bit token."""
    return secrets.token_urlsafe(32)


def hash_token(token):
    """Return the SHA-256 hash used to identify a QR token."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _fernet():
    """Create a stable encryption key derived from the application secret."""
    secret = current_app.config.get("SECRET_KEY")

    if not isinstance(secret, str) or len(secret) < 32:
        raise RuntimeError("A valid application SECRET_KEY is required for QR encryption.")

    derived_key = hashlib.sha256(
        b"nexora-qr-encryption-v1:" + secret.encode("utf-8")
    ).digest()

    return Fernet(base64.urlsafe_b64encode(derived_key))


def encrypt_token(token):
    """Encrypt a raw QR token for secure database storage."""
    return _fernet().encrypt(token.encode("ascii")).decode("ascii")


def decrypt_token(encrypted_token):
    """Recover a raw QR token from its encrypted database value."""
    try:
        token = _fernet().decrypt(
            encrypted_token.encode("ascii")
        ).decode("ascii")
    except (InvalidToken, ValueError, UnicodeError, TypeError):
        raise ServiceError(
            "qr_unavailable",
            "The saved QR code cannot be recovered securely. Please start a new attendance session.",
            409,
        )

    if not TOKEN_RE.fullmatch(token):
        raise ServiceError(
            "qr_unavailable",
            "The saved QR code is invalid and cannot be recovered.",
            409,
        )

    return token


def build_payload(token):
    """Build the text encoded inside the QR image."""
    return PREFIX + token


def parse_payload(raw):
    """Extract the token from scanned text, or raise qr_invalid."""
    text = raw.strip() if isinstance(raw, str) else ""

    if (
        len(text) > 200
        or not text.startswith(PREFIX)
        or not TOKEN_RE.fullmatch(text[len(PREFIX):])
    ):
        raise ServiceError(
            "qr_invalid",
            "This is not a valid NEXORA attendance QR code.",
            400,
        )

    return text[len(PREFIX):]


def issue_token(session_id, validity_seconds, now):
    """Create one encrypted token row. Returns (raw_token, expires_at)."""
    token = new_token()
    encrypted = encrypt_token(token)
    expires_at = now + timedelta(seconds=validity_seconds)

    execute(
        "INSERT INTO attendance_qr_tokens "
        "(session_id, token_hash, encrypted_token, issued_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            session_id,
            hash_token(token),
            encrypted,
            now,
            expires_at,
        ),
    )

    return token, expires_at


def render_png_data_uri(payload):
    """Render the QR image as a data URI."""
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(
        error_correction=ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    buffer = io.BytesIO()
    qr.make_image(
        fill_color="black",
        back_color="white",
    ).save(buffer, format="PNG")

    return "data:image/png;base64," + base64.b64encode(
        buffer.getvalue()
    ).decode("ascii")
