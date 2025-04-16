from pydantic import BaseModel, Field


class DCResponseSchema(BaseModel):
    owners: int | None
    vehicle: str | None
    mileage: int | None
    accident_count: int | None
    retail: int | None
    price: float | None
    year: int | None
    make: str | None
    model: str | None
    drivetrain: str | None
    fuel: str | None
    body_style: str | None
