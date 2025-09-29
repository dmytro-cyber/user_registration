from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FilterBase(BaseModel):
    make: str | None = None
    model: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    odometer_min: int | None = None
    odometer_max: int | None = None


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


class ROIBaseSchema(BaseModel):
    roi: float


class ROICreateSchema(ROIBaseSchema):
    pass


class ROIResponseSchema(ROIBaseSchema):
    id: int | None = None
    profit_margin: float
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class ROIListResponseSchema(BaseModel):
    roi: list[ROIResponseSchema]
