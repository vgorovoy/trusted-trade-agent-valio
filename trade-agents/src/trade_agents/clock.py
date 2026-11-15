"""Explicit demo clock; normal processes always keep real time.

Callers supply their local wall clock so existing benchmark clock injection remains
independent of this optional CLI/environment mode.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from .config import TZ


def demo_time() -> datetime | None:
    value = os.getenv("TTA_DEMO_CLOCK", "").strip()
    if not value:
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("TTA_DEMO_CLOCK must be an ISO datetime with timezone") from None
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("TTA_DEMO_CLOCK must include a timezone offset")
    return at.astimezone(TZ)


def current_time(wall_time: datetime) -> datetime:
    return demo_time() or wall_time


def spend_wall_since(window_hours: float) -> datetime | None:
    """Under a frozen demo clock, the real-time start of a spend window; otherwise None.

    Business time never moves in the demo, so a window on it would keep every past order;
    shared demos count the window in real time instead (orders record received_at_wall)."""
    if demo_time() is None:
        return None
    from datetime import UTC
    from time import time
    return datetime.fromtimestamp(time(), UTC) - timedelta(hours=window_hours)
