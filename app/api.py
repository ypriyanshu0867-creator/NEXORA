"""Consistent JSON envelope for every /api endpoint.

Success: {"ok": true,  "data": {...}}
Failure: {"ok": false, "error": {"code": "...", "message": "...", "fields": {...}?}, "retry_after": n?}
"""
import datetime as _dt
from decimal import Decimal

from flask import jsonify, request


def api_ok(data=None, status=200):
    response = jsonify({"ok": True, "data": {} if data is None else to_jsonable(data)})
    response.status_code = status
    return response


def api_error(code, message, status=400, retry_after=None, fields=None):
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    body = {"ok": False, "error": error}
    if retry_after is not None:
        body["retry_after"] = retry_after
    response = jsonify(body)
    response.status_code = status
    if retry_after is not None and status in (429, 503):
        response.headers["Retry-After"] = str(int(retry_after))
    return response


def json_body():
    """The request's JSON object, or None if missing/malformed/not an object."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def to_jsonable(value):
    """Recursively make service results JSON-safe. Decimals become strings (exact),
    datetimes become ISO-8601 UTC strings, dates become YYYY-MM-DD."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, _dt.datetime):
        return value.isoformat() + "Z"
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value
