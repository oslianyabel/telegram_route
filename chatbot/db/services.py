import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import sqlalchemy
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from chatbot.db.schema import (
    deposit_reminders_table,
    init_db,
    message_table,
    users_table,
)

logger = logging.getLogger(__name__)


class Services:
    def __init__(self, database, debug=False):
        self.database = database
        self.debug = debug

    async def get_user(self, phone: str):
        query = users_table.select().where(users_table.c.phone == phone)
        if self.debug:
            logger.debug(query)

        user = await self.database.fetch_one(query)
        return user

    async def get_all_users(self):
        query = users_table.select()
        if self.debug:
            logger.debug(query)

        users = await self.database.fetch_all(query)
        return users

    def _normalize_user_data(self, **kwargs) -> dict:
        """Normaliza y filtra datos de usuario, eliminando None y espacios."""
        normalized = {}
        for key, value in kwargs.items():
            if value is not None:
                normalized[key] = value.strip() if isinstance(value, str) else value
        return normalized

    async def create_user(
        self, phone: str, permissions: str = "user", **kwargs
    ) -> bool:
        data = {"phone": phone, "permissions": permissions}
        data.update(self._normalize_user_data(**kwargs))
        ok = await self._create_user_with_data(phone, data)
        return ok

    async def _create_user_with_data(self, phone: str, data: dict) -> bool:
        data["phone"] = phone
        query = users_table.insert().values(data)
        if self.debug:
            logger.debug(query)

        try:
            await self.database.execute(query)
        except asyncpg.exceptions.UniqueViolationError:  # llave duplicada
            logger.warning(f"create_user: {phone} already exists in the database")
            return False

        logger.debug(f"{phone} created in the database")
        return True

    async def _update_user_data(self, phone: str, data: dict) -> bool:
        data["updated_at"] = sqlalchemy.func.now()
        query = users_table.update().where(users_table.c.phone == phone).values(**data)
        if self.debug:
            logger.debug(query)

        try:
            await self.database.execute(query)
            logger.debug(f"{phone} updated in the database")
            return True
        except Exception as exc:
            logger.error(exc)
            return False

    async def update_user(self, phone: str, **kwargs) -> bool:
        update_data = self._normalize_user_data(**kwargs)

        if not update_data:
            logger.warning(f"update_user: invalid data for update {phone}")
            return False

        return await self._update_user_data(phone, update_data)

    async def create_or_update_user(
        self, phone: str, permissions: str = "user", **kwargs
    ) -> bool:
        created = await self.create_user(phone, permissions=permissions, **kwargs)
        if not created:
            return await self.update_user(phone, **kwargs)
        return True

    async def create_or_update_user_with_data(self, phone: str, data: dict) -> bool:
        created = await self._create_user_with_data(phone, data)
        if not created:
            return await self._update_user_data(phone, data)
        return True

    async def create_message(
        self, phone: str, role: str, message: str, tools_used: list[str] | None = None
    ):
        if not await self.get_user(phone):
            await self.create_user(phone)

        data = {
            "user_phone": phone,
            "role": role,
            "message": message,
            "active": True,
        }
        if tools_used is not None:
            data["tools_used"] = json.dumps(tools_used)

        query = message_table.insert().values(data)
        if self.debug:
            logger.debug(query)

        await self.database.execute(query)

    async def has_message(
        self,
        phone: str,
        role: str | None = None,
        message: str | None = None,
        active_only: bool = True,
    ) -> bool:
        query = message_table.select().where(message_table.c.user_phone == phone)
        if active_only:
            query = query.where(message_table.c.active.is_(True))
        if role is not None:
            query = query.where(message_table.c.role == role)
        if message is not None:
            query = query.where(message_table.c.message == message)
        query = query.limit(1)
        if self.debug:
            logger.debug(query)
        row = await self.database.fetch_one(query)
        return row is not None

    async def ensure_system_message(self, phone: str, message: str) -> None:
        exists = await self.has_message(phone=phone, role="system", message=message)
        if exists:
            return
        await self.create_message(phone=phone, role="system", message=message)

    async def reset_chat(self, phone: str):
        logger.warning(f"Logically deactivating chats from {phone}")
        user = await self.get_user(phone)
        if not user:
            return f"reset_chat: {phone} no existe"

        query = (
            message_table.update()
            .where(message_table.c.user_phone == phone)
            .where(message_table.c.active.is_(True))
            .values(active=False)
        )
        if self.debug:
            logger.debug(query)

        await self.database.execute(query)

    async def get_recent_messages(self, phone: str, hours: int = 24) -> list:
        """Return all messages for *phone* created within the last *hours* hours."""
        since: datetime = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            hours=hours
        )
        query = (
            message_table.select()
            .where(message_table.c.user_phone == phone)
            .where(message_table.c.active.is_(True))
            .where(message_table.c.created_at >= since)
            .order_by(message_table.c.created_at.asc())
        )
        if self.debug:
            logger.debug(query)
        return await self.database.fetch_all(query)

    async def get_last_user_message(self, phone: str):
        """Return the most recent message sent by the user (role='user').

        Used to verify the META WhatsApp 24-hour free-messaging window.
        """
        query = (
            message_table.select()
            .where(message_table.c.user_phone == phone)
            .where(message_table.c.role == "user")
            .where(message_table.c.active.is_(True))
            .order_by(message_table.c.created_at.desc())
            .limit(1)
        )
        if self.debug:
            logger.debug(query)
        return await self.database.fetch_one(query)

    async def get_pydantic_ai_history(
        self, phone: str, hours: int = 24
    ) -> list[ModelMessage]:
        """Return the last *hours* hours of conversation as PydanticAI ModelMessage objects.

        Reconstructs ModelRequest/ModelResponse pairs from the stored text rows so
        the agent can continue the conversation with full context.
        """
        rows = await self.get_recent_messages(phone, hours=hours)
        history: list[ModelMessage] = []
        for row in rows:
            role: str = row.role  # type: ignore[attr-defined]
            raw: str = row.message  # type: ignore[attr-defined]
            content = raw.removeprefix("Usuario - ").removeprefix("Bot - ")
            if role == "user":
                history.append(ModelRequest(parts=[UserPromptPart(content=content)]))  # type: ignore
            elif role == "assistant":
                history.append(
                    ModelResponse(
                        parts=[TextPart(content=content)], model_name="restored"
                    )
                )
            elif role == "system":
                history.append(ModelRequest(parts=[SystemPromptPart(content=content)]))
        logger.debug(
            "Loaded %d history messages for %s (last %dh)", len(history), phone, hours
        )
        return history

    async def get_messages(self, phone: str):
        query = (
            message_table.select()
            .where(message_table.c.user_phone == phone)
            .where(message_table.c.active.is_(True))
            .order_by(message_table.c.created_at.asc())
        )
        if self.debug:
            logger.debug(query)

        return await self.database.fetch_all(query)

    async def get_all_messages(self, phone: str):
        query = (
            message_table.select()
            .where(message_table.c.user_phone == phone)
            .order_by(message_table.c.created_at.asc())
        )
        if self.debug:
            logger.debug(query)

        return await self.database.fetch_all(query)

    async def get_chat(self, phone: str) -> list[dict]:
        messages_obj = await self.get_messages(phone)
        chat = []
        for msg in messages_obj:
            message_dict = {"role": msg.role, "content": msg.message}  # type: ignore
            if msg.tools_used:  # type: ignore
                message_dict["tools_used"] = json.loads(msg.tools_used)  # type: ignore
            chat.append(message_dict)
        return chat

    async def get_chat_str(self, phone: str) -> str:
        messages = await self.get_chat(phone)
        return json.dumps(messages)

    async def register_confirmed_ticket(self, ticket_id: str, phone: str) -> None:
        """Registra un ticket confirmado para el seguimiento del recordatorio de seña.

        Si el ticket ya existe (re-confirmación), no hace nada.
        """
        existing = await self.database.fetch_one(
            deposit_reminders_table.select().where(
                deposit_reminders_table.c.ticket_id == ticket_id
            )
        )
        if existing:
            logger.debug(
                "[deposit_reminders] ticket_id=%s ya registrado, ignorando", ticket_id
            )
            return
        query = deposit_reminders_table.insert().values(
            ticket_id=ticket_id,
            phone=phone,
            confirmed_at=datetime.now(UTC).replace(tzinfo=None),
            reminded_at=None,
        )
        await self.database.execute(query)
        logger.info(
            "[deposit_reminders] Ticket registrado para recordatorio: ticket_id=%s phone=%s",
            ticket_id,
            phone,
        )

    async def get_unreminded_tickets(self, cutoff: datetime) -> list:
        """Devuelve los tickets confirmados antes de *cutoff* cuyo recordatorio aún no fue enviado."""
        query = (
            deposit_reminders_table.select()
            .where(deposit_reminders_table.c.confirmed_at <= cutoff)
            .where(deposit_reminders_table.c.reminded_at.is_(None))
        )
        return await self.database.fetch_all(query)

    async def mark_deposit_reminder_sent(self, ticket_id: str) -> None:
        """Marca el recordatorio de un ticket como enviado."""
        query = (
            deposit_reminders_table.update()
            .where(deposit_reminders_table.c.ticket_id == ticket_id)
            .values(reminded_at=datetime.now(UTC).replace(tzinfo=None))
        )
        await self.database.execute(query)
        logger.info(
            "[deposit_reminders] Recordatorio marcado como enviado: ticket_id=%s",
            ticket_id,
        )


database = init_db()
services = Services(database)


if __name__ == "__main__":
    import asyncio

    async def test():
        await database.connect()
        phone = "+53 12345678"
        user = await services.get_user(phone)
        print("User:", user)
        await database.disconnect()

    asyncio.run(test())
