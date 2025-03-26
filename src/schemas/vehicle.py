from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime


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


# class CarInDBBase(CarBaseSchema):
#     id: int

#     class Config:
#         orm_mode = True


# class Car(CarInDBBase):
#     pass


class CarListResponseSchema(BaseModel):
    cars: List[CarBaseSchema]
