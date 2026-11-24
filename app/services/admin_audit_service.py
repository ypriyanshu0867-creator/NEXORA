
"""Read-only audit log queries for the admin panel."""

import json
import math

from app.db import query_all, query_one


PAGE_SIZE = 20


def list_audit_logs(
    *,
    action="",
    entity_type="",
    search="",
    date_from="",
    date_to="",
    page=1
):
    """Return filtered audit logs with pagination and filter options."""

    conditions = []
    params = []

    if action:
        conditions.append("al.action = %s")
        params.append(action)

    if entity_type:
        conditions.append("al.entity_type = %s")
        params.append(entity_type)

    if search:
        conditions.append(
            "(u.full_name LIKE %s OR u.email LIKE %s)"
        )
        search_value = f"%{search}%"
        params.extend([search_value, search_value])

    if date_from:
        conditions.append("al.created_at >= %s")
        params.append(date_from)

    if date_to:
        conditions.append(
            "al.created_at < DATE_ADD(%s, INTERVAL 1 DAY)"
        )
        params.append(date_to)

    where_sql = (
        " WHERE " + " AND ".join(conditions)
        if conditions else ""
    )

    count_row = query_one(
        """
        SELECT COUNT(*) AS total
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.user_id
        """ + where_sql,
        tuple(params)
    )

    total = int(count_row["total"]) if count_row else 0
    total_pages = max(math.ceil(total / PAGE_SIZE), 1)

    page = max(int(page or 1), 1)
    page = min(page, total_pages)

    offset = (page - 1) * PAGE_SIZE

    logs = query_all(
        """
        SELECT
            al.id,
            al.user_id,
            al.action,
            al.entity_type,
            al.entity_id,
            al.details,
            al.ip_address,
            al.created_at,
            u.full_name AS user_name,
            u.email AS user_email
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.user_id
        """ + where_sql + """
        ORDER BY al.created_at DESC, al.id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [PAGE_SIZE, offset])
    )

    for log in logs:
        details = log.get("details")

        if isinstance(details, str):
            try:
                log["details"] = json.loads(details)
            except (TypeError, ValueError):
                log["details"] = details

    actions = query_all(
        """
        SELECT DISTINCT action
        FROM audit_logs
        ORDER BY action ASC
        """
    )

    entity_types = query_all(
        """
        SELECT DISTINCT entity_type
        FROM audit_logs
        WHERE entity_type IS NOT NULL
          AND entity_type != ''
        ORDER BY entity_type ASC
        """
    )

    return {
        "logs": logs,
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "total_pages": total_pages,
        "actions": [row["action"] for row in actions],
        "entity_types": [
            row["entity_type"] for row in entity_types
        ]
    }
