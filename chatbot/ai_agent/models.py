"""Pydantic models for agent I/O and ERP API data structures.

All models that represent data coming from the ERP use Pydantic (external data).
Internal-only structures use dataclass (see dependencies.py).

ERP base: https://erp-cheese.deepzide.com
All endpoints are POST under /api/method/cheese.api.v1.<controller>.<method>

Models are derived from real Postman request/response examples located in
context/erp_in_out_examples/.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# ERP API path constants
# ---------------------------------------------------------------------------

ERP_BASE_PATH: str = "https://erp-cheese.deepzide.com/api/method/cheese.api.v1"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GoogleModel(StrEnum):
    Gemini_Flash_Latest = "google-gla:gemini-flash-latest"
    Gemini_Flash_Lite_Latest = "google-gla:gemini-flash-lite-latest"
    Gemini_Pro_Latest = "google-gla:gemini-pro-latest"
    Gemini_3_Pro_Preview = "google-gla:gemini-3-pro-preview"
    Gemini_3_Flash_Preview = "google-gla:gemini-3-flash-preview"
    Gemini_3_1_Pro_Preview = "google-gla:gemini-3.1-pro-preview"
    Gemini_3_1_Pro_Preview_Custom_Tools = (
        "google-gla:gemini-3.1-pro-preview-customtools"
    )
    Gemini_3_1_Flash_Lite_Preview = "google-gla:gemini-3.1-flash-lite-preview"


class ReservationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    COMPLETED = "completed"


class LeadStatus(StrEnum):
    OPEN = "OPEN"
    NOT_CONVERTED = "not converted"
    CONVERTED = "converted"


class ComplaintType(StrEnum):
    SERVICE = "Service"
    PRODUCT = "Product"
    INFRASTRUCTURE = "Infrastructure"
    STAFF = "Staff"
    OTHER = "Other"


class ComplaintIncidentType(StrEnum):
    LOCAL = "LOCAL"
    GENERAL = "GENERAL"


# ---------------------------------------------------------------------------
# 1. Contact
# ---------------------------------------------------------------------------


class ContactInfo(BaseModel):
    """CRM contact resolved or created by the ERP.

    ERP response fields: contact_id, full_name, phone, email, is_new.
    """

    contact_id: str
    phone: str | None = None
    name: str | None = None
    email: str | None = None
    is_new: bool | None = None
    preferred_language: str | None = None
    preferred_channel: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # ERP sends full_name instead of name
            if "full_name" in data and "name" not in data:
                data["name"] = data["full_name"]
        return data


class UpdateContactResult(BaseModel):
    """Response from contact_controller.update_contact.

    ERP response fields: contact (ContactInfo), changed_fields, audit_event_id.
    """

    contact: ContactInfo
    changed_fields: list[str] = Field(default_factory=list)
    audit_event_id: str | None = None


class UpdateContactRequest(BaseModel):
    """Body for update_contact."""

    contact_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    idempotency_key: str | None = None


# ---------------------------------------------------------------------------
# 2. Conversation
# ---------------------------------------------------------------------------


class ConversationInfo(BaseModel):
    """Persistent conversation returned by the ERP."""

    conversation_id: str
    contact_id: str | None = None
    channel: str | None = None
    status: str | None = None
    is_new: bool | None = None


class ConversationEvent(BaseModel):
    """Event appended to a conversation."""

    conversation_id: str
    event_type: str
    event_data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationEventResponse(BaseModel):
    """Response from conversation_controller.append_conversation_event.

    ERP response fields: event_id, conversation_id, event_type, created_at.
    """

    event_id: str
    conversation_id: str
    event_type: str
    created_at: str | None = None


# ---------------------------------------------------------------------------
# 3. Leads
# ---------------------------------------------------------------------------


class LeadInfo(BaseModel):
    """CRM lead record returned by lead_controller.upsert_lead.

    ERP response fields: lead_id, contact_id, status, is_new.
    Note: interest_type is NOT returned by the ERP response.
    """

    lead_id: str | None = None
    contact_id: str | None = None
    status: LeadStatus = LeadStatus.NOT_CONVERTED
    is_new: bool | None = None
    interest_type: str | None = None


# ---------------------------------------------------------------------------
# 4. Catalog – Experiences
# ---------------------------------------------------------------------------


class ExperienceListItem(BaseModel):
    """Experience item as returned by experience_controller.list_experiences.

    ERP response fields: name/id/experience_name, company, establishment,
    description, status, package_mode, individual_price, route_price,
    deposit_required.
    """

    experience_id: str
    name: str
    company: str | None = None
    establishment_id: str | None = None
    description: str | None = None
    status: str | None = None
    package_mode: str | None = None
    individual_price: float | None = None
    route_price: float | None = None
    deposit_required: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "experience_id" not in data:
                data["experience_id"] = data.get("id", data.get("name", ""))
            if "experience_name" in data and "name" not in data:
                data["name"] = data["experience_name"]
            if "establishment" in data and "establishment_id" not in data:
                est = data["establishment"]
                data["establishment_id"] = (
                    est if isinstance(est, str) else est.get("id")
                )
            if "deposit_required" in data:
                data["deposit_required"] = bool(data["deposit_required"])
        return data


# Keep legacy alias for backwards compatibility with existing tool code
Experience = ExperienceListItem


class EstablishmentRef(BaseModel):
    """Minimal establishment reference embedded in experience detail."""

    id: str
    name: str


class NextAvailability(BaseModel):
    """Next available slot embedded in experience detail."""

    slot_id: str
    date: str | None = None
    time: str | None = None
    available_capacity: int | None = None


class ExperiencePricing(BaseModel):
    """Pricing block from experience detail."""

    individual_price: float | None = None
    route_price: float | None = None


class ExperienceDeposit(BaseModel):
    """Deposit policy block from experience detail."""

    deposit_required: bool = False
    deposit_type: str | None = None
    deposit_value: float | None = None
    deposit_ttl_hours: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict) and "deposit_required" in data:
            data["deposit_required"] = bool(data["deposit_required"])
        return data


class ExperienceSettings(BaseModel):
    """Settings block from experience detail."""

    manual_confirmation: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict) and "manual_confirmation" in data:
            data["manual_confirmation"] = bool(data["manual_confirmation"])
        return data


class BookingPolicy(BaseModel):
    """Booking restrictions from experience detail."""

    cancel_until_hours_before: int | None = None
    modify_until_hours_before: int | None = None
    min_hours_before_booking: int | None = None


class ExperienceDetail(BaseModel):
    """Full experience detail from experience_controller.get_experience_detail.

    ERP response fields: experience_id, name, event_duration, company,
    establishment {id, name}, establishment_google_maps_link, description,
    status, package_mode, next_availability, pricing, deposit, settings,
    booking_policy.
    """

    experience_id: str
    name: str
    event_duration: str | None = None
    company: str | None = None
    establishment: EstablishmentRef | None = None
    establishment_google_maps_link: str | None = None
    description: str | None = None
    status: str | None = None
    package_mode: str | None = None
    next_availability: NextAvailability | None = None
    pricing: ExperiencePricing | None = None
    deposit: ExperienceDeposit | None = None
    settings: ExperienceSettings | None = None
    booking_policy: BookingPolicy | None = None


# ---------------------------------------------------------------------------
# 5. Catalog – Routes
# ---------------------------------------------------------------------------


class RouteExperienceRef(BaseModel):
    """Experience reference embedded in route list item.

    ERP response fields: id, experience, establishment.
    """

    experience_id: str
    experience_name: str | None = None
    establishment: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "experience_id" not in data:
                data["experience_id"] = data.get("id", "")
            if "experience_name" not in data:
                data["experience_name"] = data.get(
                    "experience", data.get("experience_name")
                )
        return data


class Route(BaseModel):
    """Route item as returned by route_controller.list_routes.

    ERP response fields: name/route_id/route_name, description, status,
    price_mode, price, experiences [{id, experience, establishment}],
    experiences_count.
    """

    route_id: str
    name: str
    description: str | None = None
    status: str | None = None
    price_mode: str | None = None
    total_price: float | None = None
    experiences: list[RouteExperienceRef] = Field(default_factory=list)
    experiences_count: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "route_id" not in data:
                data["route_id"] = data.get("id", data.get("name", ""))
            if "route_name" in data and "name" not in data:
                data["name"] = data["route_name"]
            if "price" in data and "total_price" not in data:
                data["total_price"] = data["price"]
        return data


class RouteExperienceDetail(BaseModel):
    """Experience embedded in route detail response.

    ERP fields: experience_id, experience_name, description, sequence,
    status, company.
    """

    experience_id: str
    experience_name: str | None = None
    description: str | None = None
    sequence: int | None = None
    status: str | None = None
    company: str | None = None


class RouteDetail(BaseModel):
    """Full route detail from route_controller.get_route_detail.

    ERP response fields: route_id, name, description, status, price_mode,
    price, deposit_required, deposit_type, deposit_value, deposit_ttl_hours,
    experiences [{experience_id, experience_name, description, sequence,
    status, company}], experiences_count.
    """

    route_id: str
    name: str
    description: str | None = None
    status: str | None = None
    price_mode: str | None = None
    total_price: float | None = None
    deposit_required: bool = False
    deposit_type: str | None = None
    deposit_value: float | None = None
    deposit_ttl_hours: int | None = None
    experiences: list[RouteExperienceDetail] = Field(default_factory=list)
    experiences_count: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "price" in data and "total_price" not in data:
                data["total_price"] = data["price"]
            if "deposit_required" in data:
                data["deposit_required"] = bool(data["deposit_required"])
        return data


# ---------------------------------------------------------------------------
# 6. Availability
# ---------------------------------------------------------------------------


class TimeSlot(BaseModel):
    """Available time slot from availability_controller.get_availability.

    ERP response fields: slot_id, date, time, max_capacity,
    available_capacity, slot_status, is_available.
    """

    slot_id: str
    date: str | None = None
    time: str | None = None
    max_capacity: int | None = None
    available_capacity: int | None = None
    slot_status: str | None = None
    is_available: bool = True


class AvailabilityResponse(BaseModel):
    """Result of availability_controller.get_availability.

    ERP response fields: experience_id, experience_name, date, slots,
    total_slots, available_slots.
    """

    experience_id: str | None = None
    experience_name: str | None = None
    date: str | None = None
    slots: list[TimeSlot] = Field(default_factory=list)
    total_slots: int | None = None
    available_slots: int | None = None


class RouteSlot(BaseModel):
    """Minimal slot embedded in route availability response."""

    slot_id: str
    time: str | None = None
    available_capacity: int | None = None


class RouteExperienceAvailability(BaseModel):
    """Availability per experience inside a route availability response."""

    experience_id: str
    experience_name: str | None = None
    sequence: int | None = None
    available: bool = False
    available_slots: list[RouteSlot] = Field(default_factory=list)
    available_slots_count: int | None = None


class RouteAvailabilityResponse(BaseModel):
    """Result of availability_controller.get_route_availability.

    ERP response fields: route_id, date, party_size, available,
    experiences [{experience_id, experience_name, sequence, available,
    available_slots [{slot_id, time, available_capacity}],
    available_slots_count}].
    """

    route_id: str | None = None
    date: str | None = None
    party_size: int | None = None
    available: bool = False
    experiences: list[RouteExperienceAvailability] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 7. Pricing and Policies
# ---------------------------------------------------------------------------


class PricingBreakdownItem(BaseModel):
    """Single item in a pricing preview breakdown.

    Supports both experience and route item types.
    """

    type: str | None = None
    experience_id: str | None = None
    experience_name: str | None = None
    route_id: str | None = None
    route_name: str | None = None
    slot_id: str | None = None
    price_mode: str | None = None
    unit_price: float | None = None
    price: float | None = None
    deposit: float | None = None
    party_size: int | None = None


class PricingPreview(BaseModel):
    """Pricing preview from pricing_controller.get_pricing_preview.

    ERP response fields: total_price, total_deposit, final_price,
    breakdown, party_size, items_count.
    """

    total_price: float | None = None
    total_deposit: float | None = None
    final_price: float | None = None
    breakdown: list[PricingBreakdownItem] = Field(default_factory=list)
    party_size: int | None = None
    items_count: int | None = None


class ModificationPolicy(BaseModel):
    """What can be modified and associated cost."""

    allowed: bool = False
    modifiable_fields: list[str] = Field(default_factory=list)
    fee: float | None = None
    message: str | None = None


class CancellationImpact(BaseModel):
    """Penalties and consequences of a cancellation."""

    allowed: bool = False
    penalty: float | None = None
    refund_amount: float | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# 8. Establishments
# ---------------------------------------------------------------------------


class EstablishmentListItem(BaseModel):
    """Establishment item from establishment_controller.list_establishments.

    ERP response fields: company_id, company_name, status, email, phone,
    website, description, experiences_count, online_experiences_count.
    """

    establishment_id: str
    name: str
    status: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    description: str | None = None
    experiences_count: int | None = None
    online_experiences_count: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "establishment_id" not in data:
                data["establishment_id"] = data.get(
                    "company_id", data.get("id", data.get("name", ""))
                )
            if "company_name" in data and "name" not in data:
                data["name"] = data["company_name"]
        return data


# Keep legacy alias for backwards compatibility
Establishment = EstablishmentListItem


class EstablishmentExperience(BaseModel):
    """Experience embedded in establishment detail response."""

    name: str
    experience_name: str | None = None
    description: str | None = None
    status: str | None = None
    individual_price: float | None = None
    route_price: float | None = None


class EstablishmentDetail(BaseModel):
    """Full establishment detail from establishment_controller.get_establishment_details.

    ERP response fields: company_id, company_name, status, email, phone,
    website, description, address, contacts, experiences,
    tickets_by_status, logo, documents, photos, links, pdfs.
    """

    establishment_id: str
    name: str
    status: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    description: str | None = None
    address: str | None = None
    contacts: list[Any] = Field(default_factory=list)
    experiences: list[EstablishmentExperience] = Field(default_factory=list)
    tickets_by_status: dict[str, int] = Field(default_factory=dict)
    logo: str | None = None
    documents: list[Any] = Field(default_factory=list)
    photos: list[Any] = Field(default_factory=list)
    links: list[Any] = Field(default_factory=list)
    pdfs: list[Any] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "establishment_id" not in data:
                data["establishment_id"] = data.get(
                    "company_id", data.get("id", data.get("name", ""))
                )
            if "company_name" in data and "name" not in data:
                data["name"] = data["company_name"]
        return data


# ---------------------------------------------------------------------------
# 9. Individual Reservations
# ---------------------------------------------------------------------------


class ReservationResponse(BaseModel):
    """Individual reservation created/returned by the ERP."""

    reservation_id: str
    status: ReservationStatus = ReservationStatus.PENDING
    experience_id: str | None = None
    experience_name: str | None = None
    date: str | None = None
    slot_id: str | None = None
    party_size: int | None = None
    confirmation_code: str | None = None
    next_steps: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "reservation_id" not in data:
                data["reservation_id"] = data.get("id", data.get("name", ""))
        return data


class PendingTicket(BaseModel):
    """Ticket created in PENDING state by lead_controller.upsert_lead."""

    ticket_id: str
    status: str = "PENDING"
    contact_id: str | None = None
    experience_id: str | None = None
    slot_id: str | None = None
    party_size: int | None = None
    total_price: float | None = None
    deposit_required: bool | None = None
    deposit_amount: float | None = None
    expires_at: str | None = None


class ReservationContactDetail(BaseModel):
    """Contact embedded in get_reservation_status response."""

    contact_id: str | None = None
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None


class ReservationExperienceDetail(BaseModel):
    """Experience embedded in get_reservation_status response."""

    experience_id: str | None = None
    name: str | None = None
    description: str | None = None


class ReservationSlotDetail(BaseModel):
    """Slot embedded in get_reservation_status response."""

    slot_id: str | None = None
    date: str | None = None
    time: str | None = None
    max_capacity: int | None = None


class ReservationStatusDetail(BaseModel):
    """Full details returned by ticket_controller.get_reservation_status."""

    ticket_id: str
    status: str | None = None
    contact: ReservationContactDetail | None = None
    experience: ReservationExperienceDetail | None = None
    slot: ReservationSlotDetail | None = None
    party_size: int | None = None
    deposit_required: bool | int | None = None
    deposit_amount: float | None = None
    expires_at: str | None = None
    conversation_id: str | None = None


class TicketSummary(BaseModel):
    """Single ticket returned in get_reservations_by_phone list."""

    name: str
    company: str | None = None
    experience: str | None = None
    slot: str | None = None
    route: str | None = None
    party_size: int | None = None
    status: str | None = None
    creation: str | None = None
    modified: str | None = None
    experience_name: str | None = None
    slot_date: str | None = None
    slot_time: str | None = None


class ReservationsListResponse(BaseModel):
    """Response from ticket_controller.get_reservations_by_phone."""

    contact: ReservationContactDetail | None = None
    tickets: list[TicketSummary] = Field(default_factory=list)
    page: int | None = None
    page_size: int | None = None
    total: int | None = None


class ModificationResult(BaseModel):
    """Result of ticket_controller.confirm_modification."""

    ticket_id: str
    status: str | None = None
    slot_id: str | None = None
    party_size: int | None = None
    changes: list[str] = Field(default_factory=list)


class CancellationResult(BaseModel):
    """Result of ticket_controller.cancel_reservation."""

    ticket_id: str
    old_status: str | None = None
    new_status: str | None = None
    slot_id: str | None = None


class ModificationPreview(BaseModel):
    """Preview of the impact of a reservation modification."""

    preview_id: str | None = None
    changes: list[dict[str, Any]] = Field(default_factory=list)
    price_delta: float | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# 9b. Route Reservations
# ---------------------------------------------------------------------------


class PendingRouteBooking(BaseModel):
    """Route booking created in PENDING state by route_booking_controller.create_route_reservation.

    ERP response fields: route_booking_id, route_id, contact_id, party_size,
    status, total_price, deposit_required, deposit_amount, tickets (list of
    ticket_id strings), tickets_count, conversation_id.
    """

    route_booking_id: str
    route_id: str | None = None
    contact_id: str | None = None
    party_size: int | None = None
    status: str = "PENDING"
    total_price: float | None = None
    deposit_required: bool = False
    deposit_amount: float | None = None
    tickets: list[str] = Field(default_factory=list)
    tickets_count: int | None = None
    conversation_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict) and "deposit_required" in data:
            data["deposit_required"] = bool(data["deposit_required"])
        return data


class RouteTicketStatus(BaseModel):
    """Ticket detail embedded in get_route_status response.

    ERP response fields: ticket_id, status, experience, slot, party_size,
    slot_date.
    """

    ticket_id: str
    status: str | None = None
    experience: str | None = None
    slot: str | None = None
    party_size: int | None = None
    slot_date: str | None = None


class RouteBookingStatus(BaseModel):
    """Full route booking status returned by route_booking_controller.get_route_status.

    ERP response fields: route_booking_id, route_id, status, tickets,
    tickets_count, confirmed_count, pending_count, total_price,
    deposit_required, deposit_amount.
    """

    route_booking_id: str
    route_id: str | None = None
    status: str | None = None
    tickets: list[RouteTicketStatus] = Field(default_factory=list)
    tickets_count: int | None = None
    confirmed_count: int | None = None
    pending_count: int | None = None
    total_price: float | None = None
    deposit_required: bool | int | None = None
    deposit_amount: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict) and "deposit_required" in data:
            data["deposit_required"] = bool(data["deposit_required"])
        return data


# ---------------------------------------------------------------------------
# 11. Payments
# ---------------------------------------------------------------------------


class PaymentInstructions(BaseModel):
    """Payment link and instructions for a ticket deposit.

    ERP endpoint: deposit_controller.get_payment_link_or_instructions
    ERP response fields: deposit_id, ticket_id, amount_required, amount_paid,
    amount_remaining, due_at, status, payment_link, instructions.
    """

    deposit_id: str
    ticket_id: str
    amount_required: float | None = None
    amount_paid: float | None = None
    amount_remaining: float | None = None
    due_at: str | None = None
    status: str | None = None
    payment_link: str | None = None
    instructions: str | None = None


class DepositPaymentResult(BaseModel):
    """Result of recording a deposit payment via deposit_controller.record_deposit_payment.

    ERP response data fields: deposit_id, ticket_id, amount_paid,
    total_amount_paid, amount_required, amount_remaining, old_status,
    new_status, verification_method, is_complete.
    """

    deposit_id: str
    ticket_id: str
    amount_paid: float
    total_amount_paid: float
    amount_required: float
    amount_remaining: float
    old_status: str
    new_status: str
    verification_method: str
    is_complete: bool


# ---------------------------------------------------------------------------
# 14. Survey and Complaints
# ---------------------------------------------------------------------------


class ComplaintResult(BaseModel):
    """Response from complaint_controller.create_complaint.

    ERP response fields: complaint_id, support_case_id, contact_id,
    ticket_id, route_booking_id, incident_type, status, created_at.
    """

    complaint_id: str
    support_case_id: str
    contact_id: str
    ticket_id: str | None = None
    route_booking_id: str | None = None
    incident_type: ComplaintIncidentType
    status: str
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Webhook event models (ERP → Bot)
# ---------------------------------------------------------------------------


class WebhookEvent(BaseModel):
    """Base model for any ERP webhook event."""

    event_type: str
    reservation_id: str | None = None
    booking_id: str | None = None
    contact_phone: str
    timestamp: datetime = Field(default_factory=datetime.now)
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# OCR – Payment receipt
# ---------------------------------------------------------------------------


class PaymentReceipt(BaseModel):
    """Structured data extracted from a payment receipt image (JPG/PNG).

    All fields are optional because some receipts may not contain all data.
    """

    amount: str | None = Field(
        None,
        description=(
            "Monto total depositado o pagado. "
            "Busca etiquetas como 'Monto depositado', 'Total', 'Monto', 'Amount'."
        ),
    )
    transaction_datetime: str | None = Field(
        None,
        description=(
            "Fecha y hora de la transacción en formato DD/MM/YYYY HH:MM:SS. "
            "Si sólo hay fecha, usa 00:00:00 como hora."
        ),
    )
    reference: str | None = Field(
        None,
        description=(
            "Número de referencia, código de barras o código de transacción "
            "que identifica de forma única la operación."
        ),
    )
    destination_account: str | None = Field(
        None,
        description="Número de cuenta, IBAN o información del destinatario.",
    )
    recipient_name: str | None = Field(
        None,
        description="Nombre de la empresa o persona que recibe el pago.",
    )
    payment_method: str | None = Field(
        None,
        description="Método de pago: Efectivo, Transferencia, Tarjeta, etc.",
    )
    branch: str | None = Field(
        None,
        description=(
            "Subagencia, sucursal o ubicación donde se realizó el pago. "
            "Busca etiquetas como 'Subagencia', 'Sucursal', 'Agencia', 'Branch'."
        ),
    )
    concept: str | None = Field(
        None,
        description="Concepto o motivo del pago.",
    )


# ---------------------------------------------------------------------------
# ERP → Bot webhook event models (incoming from ERP triggers)
# ---------------------------------------------------------------------------


class TicketDecision(StrEnum):
    """Possible outcomes for a pending reservation ticket."""

    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ERPSendMessageRequest(BaseModel):
    """Body for /erp/send-whatsapp endpoint.

    The ERP sends this when it wants to push a free-text message to a contact
    via WhatsApp. The phone is resolved from the ERP using contact_id.
    The 24-hour META free-messaging window is verified before sending.
    """

    contact_id: str
    message: str


class ERPTicketStatusRequest(BaseModel):
    """Body for /erp/ticket-status endpoint.

    The ERP sends this when a pending reservation is approved, rejected, or
    has expired, so the bot can notify the customer via WhatsApp.
    """

    contact_id: str
    ticket_id: str
    new_status: TicketDecision
    observations: str | None = None


class ERPSurveyRequest(BaseModel):
    """Body for /erp/activity-completed endpoint.

    The ERP sends this after an activity is completed so the bot can send
    a satisfaction survey to the customer via WhatsApp.
    """

    contact_id: str
    experience_id: str
    slot_id: str
    ticket_id: str
