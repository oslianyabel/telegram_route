"""Deactivate all chats: sets active=false for every message in the database.

# uv run python scripts/deactivate_all_chats.py
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

    statement = "UPDATE messages SET active = FALSE WHERE active = TRUE"

    with engine.begin() as connection:
        result = connection.execute(sqlalchemy.text(statement))
        print(f"Rows updated: {result.rowcount}")

    print("All chats have been deactivated.")


if __name__ == "__main__":
    main()
