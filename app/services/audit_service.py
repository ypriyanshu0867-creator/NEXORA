"""Audit logging. Writes go to the audit_logs table (Phase 1 schema)."""
import json

from app.db import execute


def log(action, *, user_id=None, entity_type=None, entity_id=None, details=None, ip=None):
    """Record who did what. Never put passwords or tokens in ``details``."""
    execute(
        "INSERT INTO audit_logs (user_id, action, entity_type, entity_id, details, ip_address) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, action[:60], entity_type,
         None if entity_id is None else str(entity_id)[:40],
         json.dumps(details) if details else None,
         ip))
