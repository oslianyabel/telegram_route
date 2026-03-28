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
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS active BOOLEAN",
        "UPDATE messages SET active = TRUE WHERE active IS NULL",
        "ALTER TABLE messages ALTER COLUMN active SET DEFAULT TRUE",
        "ALTER TABLE messages ALTER COLUMN active SET NOT NULL",
        (
            "CREATE INDEX IF NOT EXISTS idx_messages_user_active_created_at "
            "ON messages (user_phone, active, created_at)"
        ),
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(sqlalchemy.text(statement))
            print(f"OK: {statement}")

    print("Migration completed successfully.")


if __name__ == "__main__":
    main()
