r"""Retention for attendance verification data.

  python scripts\purge_old_events.py                 delete security/device events older than 90 days
  python scripts\purge_old_events.py --days 30
  python scripts\purge_old_events.py --scrub-distance-days 180
        also blank the stored distance/accuracy on attendance records older than 180 days
        (the attendance record itself is kept)

Run it manually or from Windows Task Scheduler. Nothing is purged by default
until you run it.
"""
import argparse
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import clock, create_app          # noqa: E402
from app.config import Config               # noqa: E402
from app.db import execute, transaction     # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Delete old verification events.")
    parser.add_argument("--days", type=int, default=90, help="keep events newer than this many days")
    parser.add_argument("--scrub-distance-days", type=int, default=0,
                        help="blank distance/accuracy older than this many days (0 = leave alone)")
    parser.add_argument("--test", action="store_true", help="use the test database")
    args = parser.parse_args()
    if args.days < 1:
        sys.exit("--days must be at least 1.")
    from app.config import TestConfig
    app = create_app(TestConfig if args.test else Config)
    with app.app_context(), transaction():
        cutoff = clock.now() - timedelta(days=args.days)
        events = execute("DELETE FROM attendance_security_events WHERE created_at < %s", (cutoff,)).rowcount
        devices = execute("DELETE FROM device_events WHERE created_at < %s", (cutoff,)).rowcount
        print(f"Deleted {events} attendance security event(s) and {devices} device event(s) "
              f"older than {args.days} day(s).")
        if args.scrub_distance_days > 0:
            scrub = clock.now() - timedelta(days=args.scrub_distance_days)
            n = execute("UPDATE attendance_records SET distance_m = NULL, gps_accuracy_m = NULL "
                        "WHERE marked_at < %s AND (distance_m IS NOT NULL OR gps_accuracy_m IS NOT NULL)",
                        (scrub,)).rowcount
            print(f"Blanked distance/accuracy on {n} record(s) older than {args.scrub_distance_days} day(s).")


if __name__ == "__main__":
    main()
