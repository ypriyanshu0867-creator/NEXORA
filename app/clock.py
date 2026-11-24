"""Single source of 'now' for attendance, QR and cooldown logic (naive UTC).

Tests replace it with FrozenClock so expiry and cooldown can be tested
without waiting. Timestamps are truncated to whole seconds to match MySQL
DATETIME precision.
"""
from datetime import datetime, timedelta, timezone

_override = None


def now():
    base = _override() if _override is not None else datetime.now(timezone.utc).replace(tzinfo=None)
    return base.replace(microsecond=0)


def set_clock(fn):
    global _override
    _override = fn


def reset_clock():
    set_clock(None)


class FrozenClock:
    """Test clock: stays put until advance() is called."""

    def __init__(self, start=None):
        self.t = start or datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


def iso(dt):
    """ISO-8601 string with a Z suffix for naive-UTC datetimes."""
    return None if dt is None else dt.isoformat() + "Z"
