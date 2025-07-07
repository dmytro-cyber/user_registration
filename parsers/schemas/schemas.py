from pydantic import BaseModel, Field
from typing import Optional


class DCResponseSchema(BaseModel):
    owners: Optional[int] = None
    mileage: Optional[int] = None
    accident_count: Optional[int] = None
    jd: Optional[int] = None
    manheim: Optional[int] = None
    d_max: Optional[int] = None
    html_data: Optional[str] = None  # raw HTML data
    error: Optional[str] = None


class UpdateCurrentBidRequestSchema(BaseModel):
    id: int
    url: str
    lot: int


class UpdateCurrentBidResponseSchema(BaseModel):
    id: int
    value: float | None = None


class UpdateCurrentBidListRequestSchema(BaseModel):
    items: list[UpdateCurrentBidRequestSchema]


class UpdateCurrentBidListResponseSchema(BaseModel):
    bids: list[UpdateCurrentBidResponseSchema]
