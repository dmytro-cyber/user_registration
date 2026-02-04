from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from models.vehicle import CarStatus, RecommendationStatus
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
    roi: float | None
    profit_margin: float | None
    current_bid: float | None
    suggested_bid: float | None
    location: str | None
    photos: List[str]
    has_correct_mileage: bool | None = None
    has_correct_vin: bool | None = None
    has_correct_accidents: bool | None = None
    liked: bool
    recommendation_status: str | None
    recommendation_status_reasons: str | None

    model_config = ConfigDict(from_attributes=True)


class CarCreate(CarBaseSchema):
    pass


class CarUpdate(CarBaseSchema):
    pass


class CarListResponseSchema(BaseModel):
    cars: List[CarBaseSchema]
    page_links: dict
    last: bool
    bid_info: dict | None = {}


class ConditionAssessmentResponseSchema(BaseModel):
    type_of_damage: str | None
    issue_description: str | None


class SalesHistoryBaseSchema(BaseModel):
    date: datetime | None = None
    source: str | None = None
    lot_number: int | None = None
    final_bid: int | None = None
    status: str | None = None


class CarCostsUpdateRequestSchema(BaseModel):
    maintenance: float | None = None
    transportation: float | None = None
    labor: float | None = None


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
    auction_name: str | None
    seller: str | None = None
    exterior_color: str | None
    body_style: str | None
    interior_color: str | None
    style_id: int | None
    date: datetime | None = None
    actual_bid: float | None = None
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
    has_correct_accidents: bool | None = None
    liked: bool | None = False
    maintenance: float | None = None
    transportation: float | None = None
    labor: float | None = None
    condition: str | None = None

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
    seller_type: str | None = None
    owners: int | None = None
    location: str | None = None
    engine_title: str | None = None
    fuel_type: str | None = None

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
    condition: str | None = None

    photos: List[PhotoSchema] = Field(default=[], exclude=True)
    photos_hd: List[PhotoSchema] = Field(default=[], exclude=True)
    condition_assessments: List[ConditionAssessmentResponseSchema] = Field(default=[], exclude=True)
    sales_history: List[SalesHistoryBaseSchema] = Field(default=[], exclude=True)


class CarUpdateSchema(BaseModel):
    avg_market_price: int | None = None
    recommendation_status: RecommendationStatus | None = None


class CarBulkCreateSchema(BaseModel):
    ivent: str
    vehicles: List[CarCreateSchema]


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
    profit_margin: float | None = None
    roi: float | None = None    
    current_bid: float | None = None
    actual_bid: float | None = None
    suggested_bid: float | None = None
    sum_of_investments: float | None = None
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
            auction_name=f"{obj.location} / {obj.seller}",
            mileage=obj.mileage,
            date=obj.date,
            lot=obj.lot,
            avg_market_price=obj.avg_market_price,
            predicted_total_investments=obj.predicted_total_investments,
            predicted_profit_margin=obj.predicted_profit_margin,
            sum_of_investments=obj.sum_of_investments,
            predicted_roi=obj.predicted_roi,
            profit_margin=obj.profit_margin,
            roi=obj.roi,
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
    makes_and_models: Dict[str, list] | None = []
    models: List[str] | None = []
    condition_assesstments: List[str] | None = None
    years: Dict[str, int] | None = []
    locations: List[str] | None = []
    mileage_range: Dict[str, int] | None = {}
    accident_count_range: Dict[str, int] | None = {}
    owners_range: Dict[str, int] | None = {}
    transmissions: List[str] | None = []
    body_styles: List[str] | None = []
    vehicle_types: List[str] | None = []
    drive_types: List[str] | None = []
    engine_cylinders: List[int] | None = []
    fuel_types: List[str] | None = []
    conditions: List[str] | None = []


class UpdateActualBidSchema(BaseModel):
    actual_bid: float | None = None
    roi: float | None = None
    profit_margin: float | None = None
    comment: str | None = None


class InvoiceResponse(BaseModel):
    id: int
    part_inventory_id: int
    file_url: Optional[str] = None
    created_at: datetime

    class Config:
        orm_mode = True


from datetime import datetime
from typing import List
from pydantic import BaseModel, Field


class CarUpsertSchema(BaseModel):
    vin: str = Field(
        ...,
        description="Vehicle Identification Number (VIN). Unique identifier of the vehicle."
    )

    vehicle: str | None = Field(
        None,
        description="Full vehicle title (usually combined make, model, and year)."
    )

    year: int | None = Field(
        None,
        description="Manufacturing year of the vehicle."
    )

    make: str | None = Field(
        None,
        description="Vehicle manufacturer (e.g. Toyota, Ford, BMW)."
    )

    model: str | None = Field(
        None,
        description="Vehicle model name."
    )

    mileage: int | None = Field(
        None,
        description="Vehicle mileage in miles."
    )

    auction: str | None = Field(
        None,
        description="Auction platform code or identifier (e.g. Copart, IAA)."
    )

    auction_name: str | None = Field(
        None,
        description="Auction type or name (buynow or auction)."
    )

    date: datetime | None = Field(
        None,
        description=(
            "Auction date and time in ISO 8601 format with UTC timezone. "
            "Timezone-aware datetime is required (e.g. '2025-12-22T11:45:20Z' "
            "or '2025-12-22T11:45:20+00:00')."
        )
    )

    lot: int | None = Field(
        None,
        description="Lot number assigned by the auction."
    )

    seller: str | None = Field(
        None,
        description="Seller name or organization."
    )

    seller_type: str | None = Field(
        None,
        description="Seller category (Insurance, Dealer, Private, etc.)."
    )

    location: str | None = Field(
        None,
        description="Auction location (city / state)."
    )

    engine_title: str | None = Field(
        None,
        description="Engine description as provided by auction (e.g. 2.0L I4, 3.5L V6)."
    )

    fuel_type: str | None = Field(
        None,
        description="Fuel type (Gasoline, Diesel, Electric, Hybrid, etc.)."
    )

    current_bid: float | None = Field(
        None,
        description="Current bid price at the auction."
    )

    link: str | None = Field(
        None,
        description="Direct URL to the vehicle auction page."
    )

    is_salvage: bool = Field(
        False,
        description="Indicates whether the vehicle has a salvage title."
    )

    engine: float | None = Field(
        None,
        description="Engine displacement in liters."
    )

    has_keys: bool | None = Field(
        False,
        description="Indicates whether the vehicle is sold with keys."
    )

    engine_cylinder: int | None = Field(
        None,
        description="Number of engine cylinders."
    )

    drive_type: str | None = Field(
        None,
        description="Drivetrain type (FWD, RWD, AWD, 4WD)."
    )

    interior_color: str | None = Field(
        None,
        description="Interior color of the vehicle."
    )

    exterior_color: str | None = Field(
        None,
        description="Exterior color of the vehicle."
    )

    body_style: str | None = Field(
        None,
        description="Body style (Sedan, SUV, Coupe, Hatchback, etc.)."
    )

    transmision: str | None = Field(
        None,
        description="Transmission type (Automatic, Manual, CVT, etc.)."
    )

    vehicle_type: str | None = Field(
        None,
        description="Vehicle category or type classification."
    )

    condition: str | None = Field(
        None,
        description="Overall vehicle condition as reported by the auction. (e.g. Run&Drive)"
    )

    photos: List[PhotoSchema] = Field(
        default=[],
        exclude=True,
        description="List of standard resolution photos (excluded from upsert payload).",
        example=[
            {"url": "https://copart.com/photo1.jpg"},
            {"url": "https://copart.com/photo2.jpg"},
            {"url": "https://copart.com/photo3.jpg"},
        ],
    )

    photos_hd: List[PhotoSchema] = Field(
        default=[],
        exclude=True,
        description="List of high-definition photos (excluded from upsert payload).",
        example=[
            {"url": "https://copart.com/photo_hd1.jpg"},
            {"url": "https://copart.com/photo2_hd.jpg"},
            {"url": "https://copart.com/photo3_hd.jpg"},
        ],
    )

    condition_assessments: List[ConditionAssessmentResponseSchema] = Field(
        default=[],
        exclude=True,
        description="Detailed condition assessment records (excluded from upsert payload).",
        example=[
            {"type_of_damage": "Primary", "issue_description": "Burn"},
            {"type_of_damage": "Secondary", "issue_description": "Front"},
        ],
    )


class FeeBase(BaseModel):
    auction: str = Field(..., example="copart")
    fee_type: str = Field(..., example="buyer_fee")
    amount: float = Field(..., ge=0)
    percent: bool = False
    price_from: Optional[float] = Field(None, ge=0)
    price_to: Optional[float] = Field(None, ge=0)


class FeeCreate(FeeBase):
    pass


class FeeUpdate(BaseModel):
    auction: Optional[str]
    fee_type: Optional[str]
    amount: Optional[float] = Field(None, ge=0)
    percent: Optional[bool]
    price_from: Optional[float] = Field(None, ge=0)
    price_to: Optional[float] = Field(None, ge=0)


class FeeRead(FeeBase):
    id: int

    class Config:
        from_attributes = True