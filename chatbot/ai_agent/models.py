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

from pydantic import BaseModel, Field, field_validator, model_validator

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


class CancellationPolicy(BaseModel):
    """Cancellation policy details returned by the ERP."""

    cancel_until_hours_before: int | None = None


class CancellationImpact(BaseModel):
    """Penalties and consequences of a cancellation.

    Maps the ERP ``pricing_controller.get_cancellation_impact`` response.
    """

    reservation_id: str | None = None
    experience_id: str | None = None
    can_cancel: bool = False
    penalty: float | None = None
    refund_amount: float | None = None
    cancellation_policy: CancellationPolicy | None = None
    consequences: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        """Accept legacy field names used by the old model."""
        if isinstance(data, dict):
            # Legacy: ``allowed`` → ``can_cancel``
            if "allowed" in data and "can_cancel" not in data:
                data["can_cancel"] = data.pop("allowed")
            # Legacy: ``message`` → ``consequences``
            if "message" in data and "consequences" not in data:
                data["consequences"] = data.pop("message")
        return data


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


class BankAccount(BaseModel):
    """Bank account entry returned inside EstablishmentDetail."""

    bank_account_id: str | None = None
    account_number: str | None = None
    bank_name: str | None = None
    currency: str | None = None
    holder: str | None = None
    iban: str | None = None


class EstablishmentDetail(BaseModel):
    """Full establishment detail from establishment_controller.get_establishment_details.

    ERP response fields: company_id, company_name, status, email, phone,
    website, description, address, contacts, experiences,
    tickets_by_status, logo, documents, photos, links, pdfs, bank_account.
    """

    establishment_id: str
    name: str
    status: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    description: str | None = None
    administrator_contact: str | None = None
    address: str | None = None
    contacts: list[Any] = Field(default_factory=list)
    experiences: list[EstablishmentExperience] = Field(default_factory=list)
    tickets_by_status: dict[str, int] = Field(default_factory=dict)
    logo: str | None = None
    documents: list[Any] = Field(default_factory=list)
    photos: list[Any] = Field(default_factory=list)
    links: list[Any] = Field(default_factory=list)
    pdfs: list[Any] = Field(default_factory=list)
    bank_account: list[BankAccount] = Field(default_factory=list)

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


class PriceImpact(BaseModel):
    """Price breakdown returned by ticket_controller.modify_reservation_preview."""

    current_price: float | None = None
    new_price: float | None = None
    price_difference: float | None = None


class ModificationPreview(BaseModel):
    """Preview returned by ticket_controller.modify_reservation_preview.

    ERP response fields: reservation_id, current_slot, current_party_size,
    new_slot, new_slot_date, new_slot_time, slot_change_allowed,
    new_party_size, party_size_change_allowed, price_impact.
    """

    reservation_id: str
    current_slot: str | None = None
    current_party_size: int | None = None
    new_slot: str | None = None
    new_slot_date: str | None = None
    new_slot_time: str | None = None
    slot_change_allowed: bool = True
    new_party_size: int | None = None
    party_size_change_allowed: bool = True
    price_impact: PriceImpact | None = None


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


class RouteTicketChange(BaseModel):
    """A single ticket change inside a route modification request.

    Used as input for route_booking_controller.modify_route_booking_preview
    and route_booking_controller.confirm_route_modification.
    """

    ticket_id: str
    new_slot: str | None = None
    party_size: int | None = None


class RouteTicketChangePreview(BaseModel):
    """Preview of a single ticket change within a route modification.

    ERP response fields per change: ticket_id, current_slot, current_party_size,
    new_slot, slot_changed, new_party_size, party_size_changed.
    """

    ticket_id: str
    current_slot: str | None = None
    current_party_size: int | None = None
    new_slot: str | None = None
    slot_changed: bool = False
    new_party_size: int | None = None
    party_size_changed: bool = False


class RouteModificationPreview(BaseModel):
    """Preview returned by route_booking_controller.modify_route_booking_preview.

    ERP request: route_booking_id, changes (list of ticket_id + new_slot/party_size).
    ERP response fields: route_booking_id, changes (list of RouteTicketChangePreview), note.
    """

    route_booking_id: str
    changes: list[RouteTicketChangePreview] = Field(default_factory=list)
    note: str | None = None


class RouteModificationResult(BaseModel):
    """Result of route_booking_controller.confirm_route_modification."""

    route_booking_id: str
    status: str | None = None
    changes_applied: list[str] = Field(default_factory=list)


class RouteCancellationResult(BaseModel):
    """Result of route_booking_controller.cancel_route_booking."""

    route_booking_id: str
    old_status: str | None = None
    new_status: str | None = None
    cancelled_tickets: list[str] = Field(default_factory=list)


class RouteActivityInput(BaseModel):
    """A single activity to add to a route booking (input payload).

    Fields: experience_id, slot_id.
    """

    experience_id: str
    slot_id: str


class RouteActivityPreviewItem(BaseModel):
    """Preview detail for an activity to be added to a route booking.

    ERP response fields: experience_id, experience_name, slot_id, date, time,
    price, deposit, party_size.
    """

    experience_id: str
    experience_name: str | None = None
    slot_id: str
    date: str | None = None
    time: str | None = None
    price: float | None = None
    deposit: float | None = None
    party_size: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for field in ("date", "time"):
                if data.get(field) == "None":
                    data[field] = None
        return data


class AddActivitiesToRoutePreview(BaseModel):
    """Preview returned by route_booking_controller.add_activities_to_route_preview.

    ERP request: route_booking_id, activities (list of {experience_id, slot_id}).
    ERP response fields: route_booking_id, activities_to_add, total_additional_price,
    total_additional_deposit, note.
    """

    route_booking_id: str
    activities_to_add: list[RouteActivityPreviewItem] = Field(default_factory=list)
    total_additional_price: float | None = None
    total_additional_deposit: float | None = None
    note: str | None = None


class AddActivitiesToRouteResult(BaseModel):
    """Result of route_booking_controller.confirm_add_activities_to_route.

    ERP response fields: route_booking_id, new_tickets, status, tickets_count.
    """

    route_booking_id: str
    new_tickets: list[str] = Field(default_factory=list)
    status: str | None = None
    tickets_count: int | None = None


# ---------------------------------------------------------------------------
# 11. Payments
# ---------------------------------------------------------------------------


class PaymentInstructions(BaseModel):
    """Payment instructions for a ticket deposit.

    ERP endpoint: deposit_controller.get_deposit_instructions
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
    receipt_file_id: str | None = None
    receipt_file_url: str | None = None


# ---------------------------------------------------------------------------
# 12. QR and Check-in
# ---------------------------------------------------------------------------


class ReservationQrData(BaseModel):
    """QR token payload returned by qr_controller.get_qr_for_reservation."""

    qr_token_id: str
    token: str
    ticket_id: str
    status: str
    expires_at: str | None = None
    qr_image_url: str = Field(min_length=1)
    is_new: bool = False


# ---------------------------------------------------------------------------
# 14. Survey and Complaints
# ---------------------------------------------------------------------------


class SurveyResult(BaseModel):
    """Response from survey_controller.submit_survey_response.

    ERP response fields: survey_id, ticket_id, rating, comment,
    answered_at, is_new, support_case_created, support_case_id.
    """

    survey_id: str
    ticket_id: str
    rating: int
    comment: str | None = None
    answered_at: str | None = None
    is_new: bool = True
    support_case_created: bool = False
    support_case_id: str | None = None


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
    date: str | None = Field(
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
    account: str | None = Field(
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
    bank_name: str | None = Field(
        None,
        description=(
            "Nombre del banco o entidad financiera a la que se realizó el pago. "
            "Busca etiquetas como 'Banco', 'Bank', 'Entidad', o el nombre del banco en el encabezado."
        ),
    )
    currency: str | None = Field(
        None,
        description=(
            "Moneda de la transacción. "
            "Busca el código ISO de tres letras (UYU, USD, EUR, etc.) o el símbolo de moneda."
        ),
    )


# ---------------------------------------------------------------------------
# ERP → Bot webhook event models (incoming from ERP triggers)
# ---------------------------------------------------------------------------


class TicketDecision(StrEnum):
    """Ticket statuses that can trigger a customer notification webhook."""

    PENDING = "PENDING"
    APPROVED = "CONFIRMED"
    CHECKED_IN = "CHECKED_IN"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ERPSendMessageRequest(BaseModel):
    """Body for /erp/send-whatsapp endpoint.

    The ERP sends this when it wants to push a free-text message to a contact
    via WhatsApp. The phone is resolved from the ERP using contact_id.
    The 24-hour META free-messaging window is verified before sending.
    """

    contact_id: str
    message: str


class ERPSendTelegramRequest(BaseModel):
    """Body for /erp/send-telegram endpoint.

    The ERP sends this when it wants to push a free-text message to a contact
    via Telegram. contact_id must be the Telegram chat ID of the recipient.
    """

    contact_id: str
    message: str


class ERPTicketStatusRequest(BaseModel):
    """Body for /erp/ticket-status endpoint.

    The ERP sends this when a reservation changes to a notifiable status so the
    bot can validate ownership and notify the customer via WhatsApp.
    """

    contact_id: str
    ticket_id: str
    new_status: TicketDecision
    observations: str | None = None

    @field_validator("new_status", mode="before")
    @classmethod
    def normalize_new_status(cls, value: Any) -> Any:
        """Normalize incoming ERP status names such as Checked-In or No-Show."""
        if not isinstance(value, str):
            return value

        normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
        if normalized == "APPROVED":
            return TicketDecision.APPROVED.value
        if normalized == "COMPLETADO":
            return TicketDecision.COMPLETED.value
        return normalized


class ERPSurveyRequest(BaseModel):
    """Body for /erp/activity-completed endpoint.

    The ERP sends this after an activity is completed so the bot can send
    a satisfaction survey to the customer via WhatsApp.
    """

    contact_id: str
    experience_id: str
    slot_id: str
    ticket_id: str


class ERPWhatsAppControlRequest(BaseModel):
    """Body for /erp/take-control/whatsapp and /erp/release-control/whatsapp.

    phone: WhatsApp phone number of the conversation to control (e.g. +59899000000).
    """

    phone: str


class ERPTelegramControlRequest(BaseModel):
    """Body for /erp/take-control/telegram and /erp/release-control/telegram.

    chat_id: Telegram chat ID of the conversation to control.
    """

    chat_id: str


# ---------------------------------------------------------------------------
# 14. Itinerary
# ---------------------------------------------------------------------------


class ReservationBrief(BaseModel):
    """Brief detail of a reservation within an itinerary."""

    reservation_id: str
    type: str
    experience_id: str | None = None
    experience_name: str | None = None
    date: str | None = None
    time: str | None = None
    status: str
    party_size: int
    qr_status: str | None = None
    checked_in: bool = False
    checked_in_at: datetime | None = None


class ItineraryItem(BaseModel):
    """A route or standalone experience in the customer's itinerary."""

    type: str
    route_id: str | None = None
    route_name: str | None = None
    reservations: list[ReservationBrief] = Field(default_factory=list)
    reservations_count: int = 0


class CustomerItinerary(BaseModel):
    """Full itinerary for a customer."""

    contact_id: str
    itinerary: list[ItineraryItem] = Field(default_factory=list)
    total_reservations: int = 0
    upcoming_count: int = 0
    completed_count: int = 0
