from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from models.vehicle import CarStatus


class PhotoSchema(BaseModel):
    url: str


class CarBaseSchema(BaseModel):
    id: int | None
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
    photos: List[str]

    model_config = ConfigDict(from_attributes=True)


class CarCreate(CarBaseSchema):
    pass


class CarUpdate(CarBaseSchema):
    pass


class CarListResponseSchema(BaseModel):
    cars: List[CarBaseSchema]
    page_links: dict
    last: bool


class ConditionAssessmentResponseSchema(BaseModel):
    part_name: str | None
    issue_description: str | None


class SalesHistoryBaseSchema(BaseModel):
    date: datetime
    source: str
    lot_number: int
    final_bid: int
    status: str


class CarDetailResponseSchema(BaseModel):
    id: int
    auction: str | None

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

    photos: List[str] = []

    # condition assessment
    condition_assessment: List[ConditionAssessmentResponseSchema] = []

    # sales history
    sales_history: List[SalesHistoryBaseSchema] = []

    model_config = ConfigDict(from_attributes=True)


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


# ---------------------------------------------------------


class PartSchema(BaseModel):
    name: str
    value: Optional[float] = None
    car_id: Optional[int] = None


class PhotoSchema(BaseModel):
    url: str
    car_id: Optional[int] = None


class ConditionAssessmentSchema(BaseModel):
    part_name: str
    issue_description: Optional[str] = None
    car_id: Optional[int] = None


class CarCreateSchema(BaseModel):
    vin: str
    vehicle: str
    year: int | None = None
    mileage: int | None = None
    auction: str | None = None
    auction_name: str | None = None
    date: datetime | None = None
    lot: int | None = None
    seller: str | None = None
    owners: int | None = None
    location: str | None = None

    accident_count: int | None = None

    has_correct_vin: bool = False
    has_correct_owners: bool = False
    has_correct_accidents: bool = False
    has_correct_mileage: bool = False

    bid: float | None = None
    actual_bid: float | None = None
    price_sold: float | None = None
    suggested_bid: float | None = None
    total_investment: float | None = None
    net_profit: float | None = None
    profit_margin: float | None = None
    roi: float | None = None

    parts_cost: float | None = None
    maintenance: float | None = None
    auction_fee: float | None = None
    transportation: float | None = None
    labor: float | None = None

    is_salvage: bool = False
    parts_needed: str | None = None

    engine: float | None = None
    has_keys: bool = False
    predicted_roi: float | None = None
    predicted_profit_margin: float | None = None

    engine_cylinder: int | None = None
    drive_type: str | None = None
    interior_color: str | None = None
    exterior_color: str | None = None
    body_style: str | None = None
    style_id: int | None = None
    transmision: str | None = None
    vehicle_type: str | None = None

    photos: List[PhotoSchema] = Field(default=[], exclude=True)
    sales_history: List[SalesHistoryBaseSchema] = Field(default=[], exclude=True)
