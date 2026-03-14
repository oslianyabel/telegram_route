from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class User(BaseModel):
    phone: str
    name: str | None = None
    email: EmailStr | None = None
    resume: str | None = None
    permissions: str | None = None
    created_at: datetime
    updated_at: datetime
    last_interaction: datetime

    model_config = ConfigDict(from_attributes=True)


class Messages(BaseModel):
    user_phone: str
    role: str | None = None
    message: str | None = None
    tools_used: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Statistics(BaseModel):
    user_phone: str
    interactions: int
    user_country: str | None = None
    user_languaje: str | None = None
    lead_status: str | None = None
    product_inquiry: bool | None = None
    order_inquiry: bool | None = None
    returns_management: bool | None = None
    error_ocurred: bool | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
