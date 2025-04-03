from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from models.vehicle import CarStatus


class PhotoSchema(BaseModel):
    url: str


class CarBaseSchema(BaseModel):
    vin: str | None
    vehicle: str | None
    year: int | None
    mileage: int | None
    auction: str | None
    auction_name: str | None
    date: datetime | None
    lot: int | None
    seller: str | None
    owners: int | None
    accident_count: int | None
    engine: float | None
    has_keys: bool | None
    predicted_roi: float | None
    predicted_profit_margin: float | None
    bid: float | None
    suggested_bid: float | None
    location: str | None
    photos: List[PhotoSchema]

    model_config = ConfigDict(from_attributes=True)


class CarCreate(CarBaseSchema):
    pass


class CarUpdate(CarBaseSchema):
    pass


class CarListResponseSchema(BaseModel):
    cars: List[CarBaseSchema]
    page_links: dict


class ConditionAssessmentResponseSchema(BaseModel):
    part_name: str | None
    issue_description: str | None


class SalesHistoryResponseSchema(BaseModel):
    date: datetime
    spurce: str
    lot_number: int
    final_bid: int
    status: str


class CarDeteilResponseSchema(BaseModel):
    # title
    vehicle: str

    # general
    vin: str
    mileage: int | None
    has_keys: bool | None
    engine_and_cylinder: str | None
    drive_type: str | None
    transmision: str | None
    vehicle_type: str | None
    exterior_color: str | None
    body_style: str | None
    interior_color: str | None
    style_id: int | None

    # condition assessment
    condition_assessment: List[ConditionAssessmentResponseSchema]

    # sales history
    sales_history: List[SalesHistoryResponseSchema]


class UpdateCarStatusSchema(BaseModel):
    car_status: CarStatus


class PartBaseScheme(BaseModel):
    name: str
    value: float


class PartRequestScheme(PartBaseScheme):
    pass


class PartResponseScheme(PartBaseScheme):
    id: int
    car_id: int

    class Config:
        from_attributes = True
