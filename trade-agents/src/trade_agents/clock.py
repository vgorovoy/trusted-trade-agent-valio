"""Explicit demo clock; normal processes always keep real time.

Callers supply their local wall clock so existing benchmark clock injection remains
independent of this optional CLI/environment mode.
"""
from __future__ import annotations

import os
from datetime import datetime

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
