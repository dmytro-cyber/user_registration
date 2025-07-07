from typing import List, Optional, Dict
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from models.vehicle import CarStatus
from schemas.user import UserResponseSchema


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
    current_bid: float | None
    suggested_bid: float | None
    location: str | None
    photos: List[str]
    has_correct_mileage: bool | None = None
    has_correct_vin: bool | None = None
    liked: bool
    recommendation_status: str | None

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
    type_of_damage: str | None
    issue_description: str | None


class SalesHistoryBaseSchema(BaseModel):
    date: datetime | None = None
    source: str | None = None
    lot_number: int | None = None
    final_bid: int | None = None
    status: str | None = None


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
    date: datetime | None = None
    lot: int | None = None
    owners: int | None = None
    accident_count: int | None = None
    link: str | None = None
    location: str | None = None
    auction_fee: float | None = None
    recommendation_status: str | None
    additional_info: dict | None = {}
    suggested_bid: float | None = None
    has_correct_mileage: bool | None = None
    has_correct_vin: bool | None = None
    liked: bool | None = False

    photos: List[str] = []

    # condition assessment
    condition_assessments: List[ConditionAssessmentResponseSchema] = []

    # sales history
    sales_history: List[SalesHistoryBaseSchema] = []

    model_config = ConfigDict(from_attributes=True)


class UpdateCarStatusSchema(BaseModel):
    car_status: CarStatus
    comment: str | None = None


class PartBaseScheme(BaseModel):
    name: str
    value: float


class PartRequestScheme(PartBaseScheme):
    pass


class PartResponseScheme(PartBaseScheme):
    id: int
    car_id: int
    suggested_bid: float | None = None

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


class CarCreateSchema(BaseModel):
    vin: str
    vehicle: str | None = None
    year: int | None = None
    make: str | None = None
    model: str | None = None
    mileage: int | None = None
    auction: str | None = None
    auction_name: str | None = None
    date: datetime | None = None
    lot: int | None = None
    seller: str | None = None
    owners: int | None = None
    location: str | None = None
    engine_title: str | None = None

    accident_count: int | None = None

    has_correct_vin: None | bool = False
    has_correct_owners: None | bool = False
    has_correct_accidents: None | bool = False
    has_correct_mileage: None | bool = False

    current_bid: float | None = None
    actual_bid: float | None = None
    price_sold: float | None = None
    suggested_bid: float | None = None
    link: str | None = None

    parts_cost: float | None = None
    maintenance: float | None = None
    auction_fee: float | None = None
    transportation: float | None = None
    labor: float | None = None

    is_salvage: bool = False
    parts_needed: str | None = None

    engine: float | None = None
    has_keys: None | bool = False

    engine_cylinder: int | None = None
    drive_type: str | None = None
    interior_color: str | None = None
    exterior_color: str | None = None
    body_style: str | None = None
    style_id: int | None = None
    transmision: str | None = None
    vehicle_type: str | None = None

    photos: List[PhotoSchema] = Field(default=[], exclude=True)
    photos_hd: List[PhotoSchema] = Field(default=[], exclude=True)
    condition_assessments: List[ConditionAssessmentResponseSchema] = Field(default=[], exclude=True)
    sales_history: List[SalesHistoryBaseSchema] = Field(default=[], exclude=True)


class CarBiddinHubResponseSchema(BaseModel):
    id: int | None = None
    vehicle: str | None = None
    auction: str | None = None
    auction_name: str | None = None
    mileage: int | None = None
    date: datetime | None = None
    lot: int | None = None
    avg_market_price: float | None = None
    predicted_total_investments: float | None = None
    predicted_profit_margin: float | None = None
    predicted_roi: float | None = None
    current_bid: float | None = None
    actual_bid: float | None = None
    suggested_bid: float | None = None
    last_user: str | None = None
    car_status: CarStatus | None = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm(cls, obj):
        last_history = obj.bidding_hub_history[0] if obj.bidding_hub_history else None
        last_user = (
            f"{last_history.user.first_name} {last_history.user.last_name}"
            if last_history and last_history.user and last_history.user.first_name and last_history.user.last_name
            else None
        )
        return cls(
            id=obj.id,
            vehicle=obj.vehicle,
            auction=obj.auction,
            auction_name=obj.auction_name,
            mileage=obj.mileage,
            date=obj.date,
            lot=obj.lot,
            avg_market_price=obj.avg_market_price,
            predicted_total_investments=obj.predicted_total_investments,
            predicted_profit_margin=obj.predicted_profit_margin,
            predicted_roi=obj.predicted_roi,
            current_bid=obj.current_bid,
            actual_bid=obj.actual_bid,
            suggested_bid=obj.suggested_bid,
            last_user=last_user,
            car_status=obj.car_status,
        )


class CarBiddinHubListResponseSchema(BaseModel):
    vehicles: List[CarBiddinHubResponseSchema]
    total_count: int
    total_pages: int


class BiddingHubHistorySchema(BaseModel):
    id: int | None = None
    action: str | None = None
    user: UserResponseSchema | None = None
    comment: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class BiddingHubHistoryListResponseSchema(BaseModel):
    history: List[BiddingHubHistorySchema]


class CarFilterOptionsSchema(BaseModel):
    auctions: List[str] | None = []
    auction_names: List[str] | None = []
    makes: List[str] | None = []
    models: List[str] | None = []
    years: Dict[str, int] | None = []
    locations: List[str] | None = []
    mileage_range: Dict[str, int] | None = {}
    accident_count_range: Dict[str, int] | None = {}
    owners_range: Dict[str, int] | None = {}


class UpdateCurrentBidSchema(BaseModel):
    current_bid: float
    comment: str | None = None


class InvoiceResponse(BaseModel):
    id: int
    part_inventory_id: int
    file_url: Optional[str] = None
    created_at: datetime

    class Config:
        orm_mode = True
