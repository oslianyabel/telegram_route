"""Direct tool commands for the Telegram bot.

Each handler calls an agent tool function directly, bypassing AI processing.
This allows power users to query the ERP catalog and manage their contact
data without going through the LLM layer.

Commands registered:
  /list_experiences          [buscar] [fecha=YYYY-MM-DD]
  /get_experience_detail     <id>
  /list_routes               [buscar]
  /get_route_detail          <id>
  /list_establishments
  /get_establishment_details <id>
  /get_availability          <experience_id> <date_from DD-MM-YYYY> <date_to DD-MM-YYYY>
  /get_route_availability    <route_id> <fecha> <personas>
  /resolve_or_create_contact
  /update_contact            nombre=X  email=X  telefono=X
  /upsert_lead               [Experience|Route]
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel
from pydantic_ai import RunContext
from telegram import Update
from telegram.ext import ContextTypes

from chatbot.ai_agent.context import webhook_context_manager
from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.instructions import resolve_or_create_contact
from chatbot.ai_agent.tools.catalog import (
    get_availability,
    get_establishment_details,
    get_experience_detail,
    get_route_availability,
    get_route_detail,
    list_establishments,
    list_experiences,
    list_routes,
)
from chatbot.ai_agent.tools.customer import update_contact, upsert_lead
from chatbot.db.services import services
from chatbot.messaging.telegram_notifier import notify_error
from chatbot.messaging.whatsapp import WhatsAppClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level ERP client — set by telegram_bot._post_init via init()
# ---------------------------------------------------------------------------
_erp_client: httpx.AsyncClient | None = None
_noop_whatsapp = WhatsAppClient()

_MAX_MSG_LEN = 4000  # Telegram limit is 4096; leave margin for Markdown escaping


def init(erp_client: httpx.AsyncClient) -> None:
    """Initialise the module's ERP client. Must be called during bot post_init."""
    global _erp_client
    _erp_client = erp_client
    logger.debug("telegram_commands: ERP client initialized")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_ctx(chat_id: str) -> RunContext[AgentDeps]:
    """Create a minimal RunContext for calling tool functions outside the agent."""
    assert _erp_client is not None, "ERP client is not initialized — call init() first"
    deps = AgentDeps(
        erp_client=_erp_client,
        db_services=services,
        whatsapp_client=_noop_whatsapp,
        webhook_context=webhook_context_manager,
        user_phone="",
        telegram_id=chat_id,
    )
    ctx: RunContext[AgentDeps] = RunContext[AgentDeps].__new__(RunContext)  # type: ignore[reportCallIssue]
    object.__setattr__(ctx, "deps", deps)
    object.__setattr__(ctx, "retry", 0)
    object.__setattr__(ctx, "tool_name", "telegram_cmd")
    object.__setattr__(ctx, "messages", [])
    return ctx


def _to_json_block(data: Any) -> str:
    """Serialize data to a Markdown code block (JSON), truncated if needed."""
    if isinstance(data, BaseModel):
        raw = data.model_dump()
    elif isinstance(data, list):
        raw = [
            item.model_dump() if isinstance(item, BaseModel) else item for item in data
        ]
    else:
        raw = data
    text = json.dumps(raw, indent=2, ensure_ascii=False, default=str)
    if len(text) > _MAX_MSG_LEN:
        text = text[: _MAX_MSG_LEN - 30] + "\n...(respuesta truncada)"
    return f"```json\n{text}\n```"


def _truncate(text: str) -> str:
    if len(text) > _MAX_MSG_LEN:
        return text[: _MAX_MSG_LEN - 30] + "\n...(respuesta truncada)"
    return text


def _parse_kwargs(args: list[str]) -> dict[str, str]:
    """Parse 'key=value' pairs from command args."""
    result: dict[str, str] = {}
    for arg in args:
        if "=" in arg:
            key, _, value = arg.partition("=")
            result[key.strip().lower()] = value.strip()
    return result


async def _resolve(ctx: RunContext[AgentDeps]) -> str | None:
    """Resolve or create contact. Returns error message string on failure."""
    try:
        await resolve_or_create_contact(ctx)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not resolve contact for telegram_id=%s: %s",
            ctx.deps.telegram_id,
            exc,
        )
        return f"No se pudo resolver tu contacto en el ERP: {exc}"


async def _send_error(
    update: Update,
    exc: Exception,
    context_str: str,
) -> None:
    """Log, notify dev, and reply with a friendly error message."""
    logger.exception("Error in command %s: %s", context_str, exc)
    await notify_error(exc, context=f"telegram_cmd | {context_str}")
    if update.message:
        await update.message.reply_text(
            f"⚠️ Error al ejecutar el comando: `{exc}`",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# /list_experiences [buscar] [fecha=YYYY-MM-DD]
# ---------------------------------------------------------------------------


async def cmd_list_experiences(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/list_experiences [buscar] [fecha=YYYY-MM-DD] — lista las experiencias del catálogo."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    args: list[str] = context.args or []

    search: str | None = None
    date: str | None = None
    positional: list[str] = []

    for arg in args:
        if arg.startswith("fecha="):
            date = arg.partition("=")[2]
        elif "=" not in arg:
            positional.append(arg)

    if positional:
        search = " ".join(positional)

    ctx = _build_ctx(chat_id)
    try:
        experiences = await list_experiences(ctx, search=search, date=date)
    except Exception as exc:
        await _send_error(
            update, exc, f"cmd_list_experiences search={search} date={date}"
        )
        return

    if not experiences:
        await update.message.reply_text(
            "No se encontraron experiencias con esos filtros."
        )
        return

    lines = [f"🧀 *Experiencias* ({len(experiences)} resultados)\n"]
    for exp in experiences:
        lines.append(f"• `{exp.experience_id}` — {exp.name}")

    lines.append(
        "\nUsa `/get_experience_detail <id>` para ver el detalle de una experiencia."
    )
    await update.message.reply_text(_truncate("\n".join(lines)), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /get_experience_detail <id>
# ---------------------------------------------------------------------------


async def cmd_get_experience_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/get_experience_detail <id> — detalle completo de una experiencia."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    args: list[str] = context.args or []

    if not args:
        await update.message.reply_text(
            "Uso: `/get_experience_detail <id>`\n"
            "Tip: obtén el id con `/list_experiences`.",
            parse_mode="Markdown",
        )
        return

    experience_id = args[0]
    ctx = _build_ctx(chat_id)
    try:
        detail = await get_experience_detail(ctx, experience_id=experience_id)
    except Exception as exc:
        await _send_error(update, exc, f"cmd_get_experience_detail id={experience_id}")
        return

    await update.message.reply_text(_to_json_block(detail), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /list_routes [buscar]
# ---------------------------------------------------------------------------


async def cmd_list_routes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/list_routes [buscar] — lista las rutas temáticas."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    args: list[str] = context.args or []
    search: str | None = " ".join(args) if args else None

    ctx = _build_ctx(chat_id)
    try:
        routes = await list_routes(ctx, search=search)
    except Exception as exc:
        await _send_error(update, exc, f"cmd_list_routes search={search}")
        return

    if not routes:
        await update.message.reply_text("No se encontraron rutas con esos filtros.")
        return

    lines = [f"🗺️ *Rutas* ({len(routes)} resultados)\n"]
    for route in routes:
        lines.append(f"• `{route.route_id}` — {route.name}")

    lines.append("\nUsa `/get_route_detail <id>` para ver el detalle de una ruta.")
    await update.message.reply_text(_truncate("\n".join(lines)), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /get_route_detail <id>
# ---------------------------------------------------------------------------


async def cmd_get_route_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/get_route_detail <id> — detalle completo de una ruta."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    args: list[str] = context.args or []

    if not args:
        await update.message.reply_text(
            "Uso: `/get_route_detail <id>`\nTip: obtén el id con `/list_routes`.",
            parse_mode="Markdown",
        )
        return

    route_id = args[0]
    ctx = _build_ctx(chat_id)
    try:
        detail = await get_route_detail(ctx, route_id=route_id)
    except Exception as exc:
        await _send_error(update, exc, f"cmd_get_route_detail id={route_id}")
        return

    await update.message.reply_text(_to_json_block(detail), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /list_establishments
# ---------------------------------------------------------------------------


async def cmd_list_establishments(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/list_establishments — lista los establecimientos asociados."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)

    ctx = _build_ctx(chat_id)
    try:
        establishments = await list_establishments(ctx)
    except Exception as exc:
        await _send_error(update, exc, "cmd_list_establishments")
        return

    if not establishments:
        await update.message.reply_text("No se encontraron establecimientos.")
        return

    lines = [f"🏠 *Establecimientos* ({len(establishments)} resultados)\n"]
    for est in establishments:
        lines.append(f"• `{est.establishment_id}` — {est.name}")

    lines.append("\nUsa `/get_establishment_details <id>` para ver el detalle.")
    await update.message.reply_text(_truncate("\n".join(lines)), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /get_establishment_details <id>
# ---------------------------------------------------------------------------


async def cmd_get_establishment_details(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/get_establishment_details <id> — perfil completo de un establecimiento."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    args: list[str] = context.args or []

    if not args:
        await update.message.reply_text(
            "Uso: `/get_establishment_details <id>`\nTip: obtén el id con `/list_establishments`.",
            parse_mode="Markdown",
        )
        return

    establishment_id = args[0]
    ctx = _build_ctx(chat_id)
    try:
        detail = await get_establishment_details(ctx, establishment_id=establishment_id)
    except Exception as exc:
        await _send_error(
            update, exc, f"cmd_get_establishment_details id={establishment_id}"
        )
        return

    await update.message.reply_text(_to_json_block(detail), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /get_availability <experience_id> <date_from DD-MM-YYYY> <date_to DD-MM-YYYY>


async def cmd_get_availability(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/get_availability <experience_id> <date_from DD-MM-YYYY> <date_to DD-MM-YYYY> — disponibilidad real de una experiencia."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    args: list[str] = context.args or []

    if len(args) < 3:  # noqa: PLR2004
        await update.message.reply_text(
            "Uso: `/get_availability <experience_id> <date_from> <date_to>`\n"
            "Ejemplo: `/get_availability exp-001 01-03-2026 31-12-2026`",
            parse_mode="Markdown",
        )
        return

    experience_id, date_from, date_to = args[0], args[1], args[2]
    ctx = _build_ctx(chat_id)
    try:
        availability = await get_availability(
            ctx, experience_id=experience_id, date_from=date_from, date_to=date_to
        )
    except Exception as exc:
        await _send_error(
            update,
            exc,
            f"cmd_get_availability exp={experience_id} date_from={date_from} date_to={date_to}",
        )
        return

    await update.message.reply_text(_to_json_block(availability), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /get_route_availability <route_id> <fecha> <personas>
# ---------------------------------------------------------------------------


async def cmd_get_route_availability(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/get_route_availability <route_id> <fecha> <personas> — disponibilidad de una ruta por grupo."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    args: list[str] = context.args or []

    if len(args) < 3:  # noqa: PLR2004
        await update.message.reply_text(
            "Uso: `/get_route_availability <route_id> <fecha> <personas>`\n"
            "Ejemplo: `/get_route_availability ruta-campo 2026-04-15 4`",
            parse_mode="Markdown",
        )
        return

    route_id, date, party_str = args[0], args[1], args[2]
    try:
        party_size = int(party_str)
    except ValueError:
        await update.message.reply_text(
            f"❌ `{party_str}` no es un número válido de personas.",
            parse_mode="Markdown",
        )
        return

    ctx = _build_ctx(chat_id)
    try:
        availability = await get_route_availability(
            ctx, route_id=route_id, date=date, party_size=party_size
        )
    except Exception as exc:
        await _send_error(
            update,
            exc,
            f"cmd_get_route_availability route={route_id} date={date} party={party_size}",
        )
        return

    await update.message.reply_text(_to_json_block(availability), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /resolve_or_create_contact — info del contacto resolviendo via ERP
# ---------------------------------------------------------------------------


async def cmd_resolve_or_create_contact(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/resolve_or_create_contact — muestra los datos de tu contacto en el ERP."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    ctx = _build_ctx(chat_id)

    error = await _resolve(ctx)
    if error:
        await update.message.reply_text(f"❌ {error}", parse_mode="Markdown")
        return

    deps = ctx.deps
    lines = [
        "👤 *Tu contacto en el ERP*\n",
        f"• *ID:* `{deps.contact_id}`",
        f"• *Nombre:* {deps.user_name or '_no registrado_'}",
        f"• *Email:* {deps.user_email or '_no registrado_'}",
        f"• *Teléfono:* {deps.user_phone or '_no registrado_'}",
        f"• *Telegram ID:* {deps.telegram_id}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /update_contact [nombre=X] [email=X] [telefono=X]
# ---------------------------------------------------------------------------


async def cmd_update_contact(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/update_contact nombre=X email=X telefono=X — actualiza tu contacto en el ERP."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    args: list[str] = context.args or []

    if not args:
        await update.message.reply_text(
            "Uso: `/update_contact [nombre=X] [email=X] [telefono=X]`\n"
            "Ejemplo: `/update_contact nombre=Ana email=ana@test.com`",
            parse_mode="Markdown",
        )
        return

    kwargs = _parse_kwargs(args)
    name: str | None = kwargs.get("nombre")
    email: str | None = kwargs.get("email")
    phone: str | None = kwargs.get("telefono")

    if not any([name, email, phone]):
        await update.message.reply_text(
            "❌ No se detectaron campos válidos. "
            "Usa `nombre=X`, `email=X` o `telefono=X`.",
            parse_mode="Markdown",
        )
        return

    ctx = _build_ctx(chat_id)
    error = await _resolve(ctx)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return

    try:
        result = await update_contact(ctx, name=name, email=email, phone=phone)
    except Exception as exc:
        await _send_error(update, exc, f"cmd_update_contact kwargs={kwargs}")
        return

    await update.message.reply_text(_to_json_block(result), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /upsert_lead [Experience|Route]
# ---------------------------------------------------------------------------


async def cmd_upsert_lead(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/upsert_lead [Experience|Route] — registra o actualiza un lead en el ERP."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    args: list[str] = context.args or []
    interest_type: str = args[0] if args else "Experience"

    ctx = _build_ctx(chat_id)
    error = await _resolve(ctx)
    if error:
        await update.message.reply_text(f"❌ {error}")
        return

    try:
        lead = await upsert_lead(ctx, interest_type=interest_type)
    except Exception as exc:
        await _send_error(update, exc, f"cmd_upsert_lead interest_type={interest_type}")
        return

    await update.message.reply_text(_to_json_block(lead), parse_mode="Markdown")
