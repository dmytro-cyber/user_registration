from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class FilterBase(BaseModel):
    make: str | None
    model: str | None = None
    year_from: int | None
    year_to: int | None
    odometer_min: int | None
    odometer_max: int | None


class FilterCreate(FilterBase):
    pass


class FilterUpdate(FilterBase):
    pass


class FilterUpdateTimestamp(BaseModel):
    updated_at: datetime


class FilterResponse(FilterBase):
    id: int
    updated_at: datetime | None

    class Config:
        from_attributes = True
