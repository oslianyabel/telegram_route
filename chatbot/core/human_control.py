"""Human control store.

Simple in-memory store that tracks which conversations are currently
under human operator control. When a conversation is controlled by a human,
AI auto-responses are silenced so the operator can write replies manually.

Two independent sets are maintained:
- ``_whatsapp_controlled``: WhatsApp phone numbers under human control.
- ``_telegram_controlled``: Telegram chat IDs under human control.
"""

import logging

logger = logging.getLogger(__name__)

_whatsapp_controlled: set[str] = set()
_telegram_controlled: set[str] = set()


def take_whatsapp_control(phone: str) -> None:
    """Block AI auto-responses for *phone* on WhatsApp."""
    _whatsapp_controlled.add(phone)
    logger.info("[human-control] WhatsApp control TAKEN for %s", phone)


def release_whatsapp_control(phone: str) -> None:
    """Re-enable AI auto-responses for *phone* on WhatsApp."""
    _whatsapp_controlled.discard(phone)
    logger.info("[human-control] WhatsApp control RELEASED for %s", phone)


def is_whatsapp_controlled(phone: str) -> bool:
    """Return True if AI responses are disabled for *phone* on WhatsApp."""
    return phone in _whatsapp_controlled


def take_telegram_control(chat_id: str) -> None:
    """Block AI auto-responses for *chat_id* on Telegram."""
    _telegram_controlled.add(chat_id)
    logger.info("[human-control] Telegram control TAKEN for chat_id=%s", chat_id)


def release_telegram_control(chat_id: str) -> None:
    """Re-enable AI auto-responses for *chat_id* on Telegram."""
    _telegram_controlled.discard(chat_id)
    logger.info("[human-control] Telegram control RELEASED for chat_id=%s", chat_id)


def is_telegram_controlled(chat_id: str) -> bool:
    """Return True if AI responses are disabled for *chat_id* on Telegram."""
    return chat_id in _telegram_controlled
