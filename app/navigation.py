"""Role-based sidebar navigation.

Each entry is (label, endpoint, icon). An entry whose endpoint does not
exist yet is shown greyed out with a "Soon" badge instead of a dead link,
and becomes a normal link automatically once that route is registered.
Later phases MUST use these endpoint names.
"""
from flask import current_app, g, request, url_for

NAV_ITEMS = {
    "student": [
        ("Dashboard", "student.dashboard", "bi-speedometer2"),
        ("Scan QR", "attendance.scan", "bi-qr-code-scan"),
        ("My Attendance", "attendance.history", "bi-calendar-check"),
        ("Academic Results", "academics.results", "bi-mortarboard"),
        ("GPA Calculator", "academics.calculator", "bi-calculator"),
        ("Library", "library.catalog", "bi-book"),
        ("Syllabus", "syllabus.index", "bi-journal-text"),
        ("Notices", "notices.index", "bi-megaphone"),
        ("Fees", "fees.my_fees", "bi-wallet2"),
        ("Profile", "student.profile", "bi-person-circle"),
    ],
    "teacher": [
        ("Dashboard", "teacher.dashboard", "bi-speedometer2"),
        ("My Classes", "teacher.classes", "bi-people"),
        ("Attendance Sessions", "attendance.teacher_sessions", "bi-qr-code"),
        ("Session History", "teacher.sessions", "bi-clock-history"),
        ("Attendance Reports", "teacher.reports", "bi-bar-chart-line"),
        ("Results", "teacher.results", "bi-mortarboard"),
        ("Syllabus & Materials", "teacher.syllabus", "bi-journal-text"),
        ("Notices", "teacher.notices", "bi-megaphone"),
    ],
    "admin": [
        ("Dashboard", "admin.dashboard", "bi-speedometer2"),
        ("Pending Approvals", "admin.pending_users", "bi-person-check"),
        ("Users", "admin.users", "bi-people"),
        ("Departments & Classes", "admin.departments", "bi-diagram-3"),
        ("Classrooms", "admin.classrooms", "bi-geo-alt"),
        ("Library", "library.manage", "bi-book"),
        ("Fees", "fees.manage", "bi-wallet2"),
        ("Notices", "notices.index", "bi-megaphone"),
        ("Audit Logs", "admin.audit_logs", "bi-shield-check"),
    ],
}

ROLE_LABELS = {"student": "Student", "teacher": "Teacher", "admin": "Administrator"}


def build_nav(role):
    items = []
    for label, endpoint, icon in NAV_ITEMS.get(role, []):
        available = endpoint in current_app.view_functions
        items.append({
            "label": label,
            "icon": icon,
            "available": available,
            "url": url_for(endpoint) if available else None,
            "active": request.endpoint == endpoint,
        })
    return items


def init_app(app):
    @app.context_processor
    def inject_navigation():
        user = g.get("user")
        if not user:
            return {"current_user": None, "nav_items": []}
        return {
            "current_user": user,
            "role_label": ROLE_LABELS.get(user["role"], user["role"]),
            "nav_items": build_nav(user["role"]),
        }
