"""Manage webhook context injected by the ERP into specific conversations.

When the ERP sends a webhook (e.g. booking confirmed, payment reminder),
the event is stored here keyed by user phone. The agent reads and clears
pending events at the start of each run so it can act on them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from chatbot.ai_agent.models import WebhookEvent

logger = logging.getLogger(__name__)


@dataclass
class WebhookContextManager:
    """Thread-safe(ish) store of pending webhook events per user phone."""

    _pending: dict[str, list[WebhookEvent]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Write side (called from the ERP webhook router)
    # ------------------------------------------------------------------

    def push_event(self, phone: str, event: WebhookEvent) -> None:
        """Enqueue a webhook event for a specific conversation."""
        self._pending.setdefault(phone, []).append(event)
        logger.info(
            "Webhook event '%s' queued for %s (booking=%s)",
            event.event_type,
            phone,
            event.booking_id,
        )

    # ------------------------------------------------------------------
    # Read side (called by the agent before each run)
    # ------------------------------------------------------------------

    def pop_events(self, phone: str) -> list[WebhookEvent]:
        """Return and clear all pending events for *phone*."""
        events = self._pending.pop(phone, [])
        if events:
            logger.info("Popped %d webhook event(s) for %s", len(events), phone)
        return events

    def has_pending(self, phone: str) -> bool:
        return bool(self._pending.get(phone))


# Singleton shared across the application
webhook_context_manager = WebhookContextManager()
