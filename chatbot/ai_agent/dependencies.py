"""Dependency injection container for the AI agent.

Provides all external services the agent needs: ERP client, DB services,
and WhatsApp client.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from chatbot.db.services import Services
from chatbot.messaging.whatsapp import WhatsAppManager


@dataclass
class AgentDeps:
    """Dependencies injected into every agent run via RunContext."""

    erp_client: httpx.AsyncClient
    db_services: Services
    whatsapp_client: WhatsAppManager
    user_phone: str = ""
    user_name: str | None = None
    user_email: str | None = None
    telegram_id: str | None = None
    contact_id: str | None = None
    conversation_id: str | None = None
    # Tracks which tools have been called in the current turn (for once-per-turn enforcement)
    called_tools: set[str] = field(default_factory=set)
    # Cache: True if the customer has at least one COMPLETED reservation (None = not yet checked)
    has_completed_reservations: bool | None = None
    # Tracks the lead_id created/updated for this contact (required before creating a reservation)
    lead_id: str | None = None
