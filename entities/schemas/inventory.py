from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import List, Optional
from enum import Enum
from models.vehicle import CarInventoryStatus, CarInventoryInvestmentsType, PartInventoryStatus


class CarInventoryBase(BaseModel):
    vehicle: str = Field(..., min_length=1)
    vin: str = Field(..., min_length=17, max_length=17)
    vehicle_cost: float = Field(..., ge=0)
    parts_cost: Optional[float] = Field(None, ge=0)
    maintenance: Optional[float] = Field(None, ge=0)
    auction_fee: Optional[float] = Field(None, ge=0)
    transportation: Optional[float] = Field(None, ge=0)
    labor: Optional[float] = Field(None, ge=0)
    additional_costs: Optional[float] = Field(None, ge=0)
    car_status: str = Field(..., min_length=1)

    class Config:
        orm_mode = True


class CarInventoryCreate(CarInventoryBase):
    comment: Optional[str] = Field(None, min_length=1)


class CarInventoryUpdate(CarInventoryBase):
    vehicle: Optional[str] = Field(None, min_length=1)
    vin: Optional[str] = Field(None, min_length=17, max_length=17)
    car_status: Optional[CarInventoryStatus] = None
    comment: Optional[str] = Field(None, min_length=1)


class CarInventoryUpdateStatus(BaseModel):
    status: CarInventoryStatus
    comment: Optional[str] = Field(None, min_length=1)


class CarInventoryResponse(CarInventoryBase):
    id: int
    purchase_date: datetime
    stock: str
    net_profit: Optional[float]
    total_investments: float
    roi: float
    profit_margin_percent: float
    investments: List["CarInventoryInvestmentsResponse"]
    comment: Optional[str] = None  # Додано поле для коментаря


class CarInventoryInvestmentsBase(BaseModel):
    vendor: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    cost: float = Field(..., ge=0)
    payment_method: str = Field(..., min_length=1)
    investment_type: CarInventoryInvestmentsType = None

    class Config:
        orm_mode = True


class CarInventoryInvestmentsCreate(CarInventoryInvestmentsBase):
    comment: Optional[str] = Field(None, min_length=1)


class CarInventoryInvestmentsUpdate(CarInventoryInvestmentsBase):
    vendor: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = Field(None, min_length=1)
    cost: Optional[float] = Field(None, ge=0)
    payment_method: Optional[str] = Field(None, min_length=1)
    investment_type: Optional[CarInventoryInvestmentsType] = None
    comment: Optional[str] = Field(None, min_length=1)


class CarInventoryInvestmentsResponse(CarInventoryInvestmentsBase):
    id: int
    date: datetime
    car_inventory_id: int
    comment: Optional[str] = None  # Додано поле для коментаря


class PartInventoryCreate(BaseModel):
    vehicle: str
    part_description: str
    supplier: str
    price: float
    comment: Optional[str] = None

    @validator("price")
    def validate_price(cls, value):
        if value < 0:
            raise ValueError("Price cannot be negative")
        return value


class PartInventoryUpdate(BaseModel):
    vehicle: Optional[str] = None
    part_description: Optional[str] = None
    supplier: Optional[str] = None
    price: Optional[float] = None
    comment: Optional[str] = None

    @validator("price")
    def validate_price(cls, value):
        if value is not None and value < 0:
            raise ValueError("Price cannot be negative")
        return value


class PartInventoryResponse(BaseModel):
    id: int
    vehicle: str
    part_description: str
    supplier: str
    price: float
    part_status: PartInventoryStatus
    invoices: List["InvoiceResponse"] = []
    fullname: Optional[str] = None
    comment: Optional[str] = None

    class Config:
        orm_mode = True


class InvoiceResponse(BaseModel):
    id: int
    part_inventory_id: int
    file_url: Optional[str] = None
    created_at: datetime

    class Config:
        orm_mode = True


class HistoryResponse(BaseModel):
    id: int
    created_at: datetime
    action: str
    user_id: int
    car_id: Optional[int] = None
    car_inventory_id: Optional[int] = None
    part_inventory_id: Optional[int] = None
    comment: Optional[str] = None

    class Config:
        orm_mode = True


class PartInventoryStatusUpdate(BaseModel):
    part_status: PartInventoryStatus
    comment: Optional[str] = None