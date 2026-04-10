from __future__ import annotations

from datetime import datetime, time


def parse_slot_time(raw: str) -> time | None:
    """Parsea el horario del slot desde el formato del ERP (ej: '9:00:00')."""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None
