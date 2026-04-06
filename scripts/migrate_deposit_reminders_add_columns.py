"""Migration: add reminder_count and ticket_date columns to deposit_reminders table.

# uv run python scripts/migrate_deposit_reminders_add_columns.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.core.config import config


def _normalized_db_url() -> str:
    db_url = config.DATABASE_URL  # type: ignore[attr-defined]
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql://", 1)
    return db_url


def main() -> None:
    engine = sqlalchemy.create_engine(_normalized_db_url())

    statements = [
        # Columna para contar los recordatorios enviados (max 3 por spec)
        "ALTER TABLE deposit_reminders ADD COLUMN IF NOT EXISTS reminder_count INTEGER NOT NULL DEFAULT 0",
        # Columna para guardar la fecha del ticket (filtra tickets con fecha pasada)
        "ALTER TABLE deposit_reminders ADD COLUMN IF NOT EXISTS ticket_date TIMESTAMP",
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(sqlalchemy.text(statement))
            print(f"OK: {statement}")

    print("Migration completed successfully.")


if __name__ == "__main__":
    main()
