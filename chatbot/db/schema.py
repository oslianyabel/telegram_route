import logging
import time

import databases
import sqlalchemy
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func

from chatbot.core.config import config

logger = logging.getLogger(__name__)

metadata = sqlalchemy.MetaData()

users_table = sqlalchemy.Table(
    "users",
    metadata,
    sqlalchemy.Column("phone", String, primary_key=True),
    sqlalchemy.Column("name", String, nullable=True),
    sqlalchemy.Column("address", String, nullable=True),
    sqlalchemy.Column("resume", Text, nullable=True),
    sqlalchemy.Column("permissions", String, default="user"),
    sqlalchemy.Column("created_at", DateTime, default=func.now()),
    sqlalchemy.Column("updated_at", DateTime, default=func.now()),
    sqlalchemy.Column("last_interaction", DateTime, default=func.now()),
)

message_table = sqlalchemy.Table(
    "messages",
    metadata,
    sqlalchemy.Column("id", Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_phone", ForeignKey("users.phone"), nullable=False),
    sqlalchemy.Column("role", String, nullable=False),
    sqlalchemy.Column("message", String, nullable=False),
    sqlalchemy.Column("tools_used", Text, nullable=True),
    sqlalchemy.Column(
        "active", Boolean, nullable=False, server_default=sqlalchemy.true()
    ),
    sqlalchemy.Column("created_at", DateTime, default=func.now()),
)

deposit_reminders_table = sqlalchemy.Table(
    "deposit_reminders",
    metadata,
    sqlalchemy.Column("ticket_id", String, primary_key=True),
    sqlalchemy.Column("phone", String, nullable=False),
    sqlalchemy.Column("confirmed_at", DateTime, nullable=False),
    sqlalchemy.Column("reminded_at", DateTime, nullable=True),
    sqlalchemy.Column("reminder_count", Integer, default=0, server_default="0"),
    sqlalchemy.Column("ticket_date", DateTime, nullable=True),
)

# Database connection retry settings
DB_MAX_RETRIES = 5
DB_RETRY_DELAY = 3  # seconds


def init_db():
    db_url = config.DATABASE_URL  # type: ignore
    # Fix for SQLAlchemy: postgres:// is not valid, must be postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    logger.info(
        f"🔌 Initializing database connection to: {db_url.split('@')[1] if '@' in db_url else 'unknown'}"
    )

    engine = sqlalchemy.create_engine(db_url)

    # Retry logic for create_all (waits for DB to be ready)
    for attempt in range(1, DB_MAX_RETRIES + 1):
        try:
            logger.info(
                f"🔄 Attempting to create tables (attempt {attempt}/{DB_MAX_RETRIES})..."
            )
            metadata.create_all(engine)
            logger.info("✅ Database tables created/verified successfully")
            break
        except Exception as exc:
            logger.error(
                f"❌ Database connection failed (attempt {attempt}/{DB_MAX_RETRIES}): {exc}"
            )
            if attempt < DB_MAX_RETRIES:
                logger.info(f"⏳ Retrying in {DB_RETRY_DELAY} seconds...")
                time.sleep(DB_RETRY_DELAY)
            else:
                logger.error(
                    f"💀 All {DB_MAX_RETRIES} database connection attempts failed"
                )
                raise

    database = databases.Database(db_url, force_rollback=False)
    return database
