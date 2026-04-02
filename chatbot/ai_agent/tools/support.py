"""Tools for handling customer complaints in the ERP."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from pydantic_ai import ModelRetry, RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    ComplaintIncidentType,
    ComplaintResult,
    ComplaintType,
    ReservationStatus,
    SurveyResult,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data

logger = logging.getLogger(__name__)

ERP_TIMEOUT_SECONDS = 15.0


async def create_complaint(
    ctx: RunContext[AgentDeps],
    description: str,
    complaint_type: ComplaintType = ComplaintType.SERVICE,
    incident_type: ComplaintIncidentType = ComplaintIncidentType.LOCAL,
) -> ComplaintResult:
    """Register a support case or complaint in the ERP on behalf of the customer.

    Invoke this tool in ANY of the following situations:

    1. LATE ARRIVAL — The customer warns they will arrive late to a booked event.
       Use complaint_type=Service, incident_type=LOCAL. Include the expected
       delay and the relevant ticket_id in description.

    2. ESCALATION TO HUMAN — The customer's query cannot be resolved by the
       assistant and needs to be handled by a human expert (e.g. special
       accommodations, complex complaints, billing disputes).
       Use complaint_type=Service, incident_type=LOCAL or REMOTE depending on
       whether the issue is about an on-site or remote interaction.

    3. COMPLAINT OR SUGGESTION about an experience or route — The customer
       reports a bad experience, gives negative feedback, or makes a suggestion
       for improvement.
       Use complaint_type=Service for service issues, complaint_type=Staff for
       staff-related feedback, complaint_type=Product for product quality.
       Set incident_type=LOCAL.

    4. CHATBOT COMMUNICATION PROBLEM — The customer reports that the assistant
       gave wrong information, failed to understand them, or behaved unexpectedly.
       Use complaint_type=Other, incident_type=GENERAL.

    Always confirm with the user before calling this tool and
    tell them that a support case has been opened once the complaint is registered.

    Args:
        ctx: Agent run context with dependencies.
        description: Clear, detailed description of the issue. Include relevant
            context such as booking IDs, experience names, or timestamps.
        complaint_type: Category that best fits the issue (Service, Product,
            Infrastructure, Staff, Other).
        incident_type: LOCAL if the incident occurred or will occur on-site;
            GENERAL if it happened through the chat or a digital channel.
    """
    logger.info(
        "[create_complaint] contact_id=%s complaint_type=%s incident_type=%s",
        ctx.deps.contact_id,
        complaint_type,
        incident_type,
    )
    if not ctx.deps.contact_id:
        raise ValueError("contact_id is required in AgentDeps to create a complaint")

    payload: dict[str, Any] = {
        "contact_id": ctx.deps.contact_id,
        "description": description,
        "complaint_type": complaint_type.value,
        "incident_type": incident_type.value,
    }

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.complaint_controller.create_complaint",
        json=payload,
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data: dict[str, Any] = extract_erp_data(response.json())

    result = ComplaintResult.model_validate(data)
    logger.info(
        "Complaint created: %s – status=%s",
        result.complaint_id,
        result.status,
    )
    return result


async def submit_survey(
    ctx: RunContext[AgentDeps],
    ticket_id: str,
    rating: int,
    comment: str | None = None,
) -> SurveyResult:
    """Submit a satisfaction survey after completing an experience or route.

    Call this tool when the customer wants to rate or comment on an experience
    they have already completed. Ask for a rating from 1 to 5 and an optional
    written comment before calling this tool.

    Args:
        ctx: Agent run context with dependencies.
        ticket_id: Reservation ticket ID associated with the completed experience
            (e.g. "TKT-2026-03-00078").
        rating: Satisfaction score from 1 (very bad) to 5 (excellent).
        comment: Optional written feedback from the customer.
    """
    logger.info(
        "[submit_survey] ticket_id=%s rating=%s",
        ticket_id,
        rating,
    )
    if not 1 <= rating <= 5:
        raise ModelRetry(
            f"rating must be between 1 and 5, got {rating}. "
            "Pídele al usuario una puntuación válida entre 1 y 5 antes de reenviar la encuesta."
        )

    # Validate that the reservation date is not in the future
    status_response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.ticket_controller.get_reservation_status",
        json={"reservation_id": ticket_id},
        timeout=ERP_TIMEOUT_SECONDS,
    )
    status_response.raise_for_status()
    status_data: dict[str, Any] = extract_erp_data(status_response.json())

    reservation_status = (status_data.get("status") or "").strip().lower()
    if reservation_status != ReservationStatus.COMPLETED.value:
        raise ModelRetry(
            f"Cannot submit a survey for a reservation that is not completed yet "
            f"(ticket_id={ticket_id}, status={status_data.get('status')}). "
            "Explícale al usuario que solo se pueden registrar encuestas de reservas completadas "
            "y pídele otro ticket si corresponde."
        )

    slot_date_str: str | None = (status_data.get("slot") or {}).get("date")
    if slot_date_str:
        slot_date = date.fromisoformat(slot_date_str)
        if slot_date > date.today():
            raise ModelRetry(
                f"Cannot submit a survey for a reservation that has not taken place yet "
                f"(ticket_id={ticket_id}, scheduled_date={slot_date_str}). "
                "Indícale al usuario que espere a completar la experiencia antes de enviar la encuesta."
            )

    payload: dict[str, Any] = {
        "ticket_id": ticket_id,
        "rating": rating,
    }
    if comment:
        payload["comment"] = comment

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.survey_controller.submit_survey_response",
        json=payload,
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data: dict[str, Any] = extract_erp_data(response.json())

    result = SurveyResult.model_validate(data)
    logger.info(
        "Survey submitted: survey_id=%s ticket_id=%s rating=%s support_case_created=%s",
        result.survey_id,
        result.ticket_id,
        result.rating,
        result.support_case_created,
    )
    return result
