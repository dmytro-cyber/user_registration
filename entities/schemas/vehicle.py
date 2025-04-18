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
    photos: List[str]

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


class CarSaleHistorySchema(BaseModel):
    date: datetime
    source: str
    lot_number: int
    final_bid: Optional[int] = None
    status: Optional[str] = None
    car_id: Optional[int] = None


class CarCreateSchema(BaseModel):
    vin: str
    vehicle: str
    year: Optional[int] = None
    mileage: Optional[int] = None
    auction: Optional[str] = None
    auction_name: Optional[str] = None
    date: Optional[datetime] = None
    lot: Optional[int] = None
    seller: Optional[str] = None
    owners: Optional[int] = None
    location: Optional[str] = None

    accident_count: Optional[int] = None

    has_correct_vin: bool = False
    has_correct_owners: bool = False
    has_correct_accidents: bool = False
    has_correct_mileage: bool = False

    bid: Optional[float] = None
    actual_bid: Optional[float] = None
    price_sold: Optional[float] = None
    suggested_bid: Optional[float] = None
    total_investment: Optional[float] = None
    net_profit: Optional[float] = None
    profit_margin: Optional[float] = None
    roi: Optional[float] = None

    parts_cost: Optional[float] = None
    maintenance: Optional[float] = None
    auction_fee: Optional[float] = None
    transportation: Optional[float] = None
    labor: Optional[float] = None

    is_salvage: bool = False
    parts_needed: Optional[str] = None

    engine: Optional[float] = None
    has_keys: bool = False
    predicted_roi: Optional[float] = None
    predicted_profit_margin: Optional[float] = None

    engine_cylinder: Optional[int] = None
    drive_type: Optional[str] = None
    interior_color: Optional[str] = None
    exterior_color: Optional[str] = None
    body_style: Optional[str] = None
    style_id: Optional[int] = None
    transmision: Optional[str] = None
    vehicle_type: Optional[str] = None

    photos: List[PhotoSchema] = []
