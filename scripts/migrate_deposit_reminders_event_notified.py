"""Migration: add slot_time and event_notified columns to deposit_reminders table.

# uv run python scripts/migrate_deposit_reminders_event_notified.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.core.config import config


def _normalized_db_url() -> str:
    db_url: str = config.DATABASE_URL  # type: ignore[attr-defined]
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql://", 1)
    return db_url


def main() -> None:
    engine = sqlalchemy.create_engine(_normalized_db_url())

    statements: list[str] = [
        "ALTER TABLE deposit_reminders ADD COLUMN IF NOT EXISTS slot_time VARCHAR",
        "ALTER TABLE deposit_reminders ADD COLUMN IF NOT EXISTS event_notified BOOLEAN NOT NULL DEFAULT FALSE",
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(sqlalchemy.text(statement))
            print(f"OK: {statement}")

    print("Migration completed successfully.")


if __name__ == "__main__":
    main()
