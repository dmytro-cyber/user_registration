from pydantic import BaseModel, Field
from typing import Optional


class DCResponseSchema(BaseModel):
    owners: Optional[int] = None
    vehicle: Optional[str] = None
    mileage: Optional[int] = None
    accident_count: Optional[int] = None
    retail: Optional[str] = None
    manheim: Optional[str] = None
    price: Optional[str] = None
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    drivetrain: Optional[str] = None
    fuel: Optional[str] = None
    body_style: Optional[str] = None
    screenshot: Optional[str] = None  # Base64-encoded screenshot
    error: Optional[str] = None


class UpdateCurrentBidRequestSchema(BaseModel):
    id: int
    url: str
    lot: int


class UpdateCurrentBidResponseSchema(BaseModel):
    id: int
    current_bid: float | None = None


class UpdateCurrentBidListRequestSchema(BaseModel):
    items: list[UpdateCurrentBidRequestSchema]


class UpdateCurrentBidListResponseSchema(BaseModel):
    items: list[UpdateCurrentBidResponseSchema]
