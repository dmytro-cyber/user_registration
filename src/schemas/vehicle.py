from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime


class Photo(BaseModel):
    id: int
    url: str


class CarBase(BaseModel):
    vin: str
    vehicle: str
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
    photos: List[Photo] | None

    model_config = ConfigDict(from_attributes=True)


class CarCreate(CarBase):
    pass


class CarUpdate(CarBase):
    pass


class CarInDBBase(CarBase):
    id: int

    class Config:
        orm_mode = True


class Car(CarInDBBase):
    pass


class CarListResponseSchema(BaseModel):
    cars: List[CarBase]
