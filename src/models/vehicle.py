from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from models import Base
import enum


class RecommendationStatus(enum.Enum):
    RECOMMENDED = "recommended"
    NOT_RECOMMENDED = "not_recommended"


class CarStatus(enum.Enum):
    AT_AUCTION = "at_auction"
    PURCHASED = "purchased"
    IN_REPAIR = "in_repair"
    READY_FOR_SALE = "ready_for_sale"
    SOLD = "sold"


class Car(Base):
    __tablename__ = "cars"

    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    vehicle = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    mileage = Column(Integer, nullable=True, index=True)
    auction = Column(String, nullable=True)
    auction_name = Column(String, nullable=True)
    date = Column(DateTime, nullable=True)
    lot = Column(Integer, nullable=True)
    seller = Column(String, nullable=True)
    owners = Column(Integer, nullable=True)
    location = Column(String, nullable=True)

    accident_count = Column(Integer, nullable=True)

    # Additional checks
    has_correct_vin = Column(Boolean, nullable=False, default=False)
    has_correct_owners = Column(Boolean, nullable=False, default=False)
    has_correct_accidents = Column(Boolean, nullable=False, default=False)
    has_correct_mileage = Column(Boolean, nullable=False, default=False)

    # Financials
    bid = Column(Float, nullable=True)
    actual_bid = Column(Float, nullable=True)
    price_sold = Column(Float, nullable=True)
    suggested_bid = Column(Float, nullable=True)
    total_investment = Column(Float, nullable=True)
    net_profit = Column(Float, nullable=True)
    profit_margin = Column(Float, nullable=True)
    roi = Column(Float, nullable=True)

    # Additional costs
    parts_cost = Column(Float, nullable=True)
    maintenance = Column(Float, nullable=True)
    auction_fee = Column(Float, nullable=True)
    transportation = Column(Float, nullable=True)
    labor = Column(Float, nullable=True)

    # Vehicle condition
    is_salvage = Column(Boolean, default=False)
    parts_needed = Column(String, nullable=True)

    # Status
    recommendation_status = Column(
        Enum(RecommendationStatus), nullable=False, default=RecommendationStatus.NOT_RECOMMENDED
    )
    car_status = Column(Enum(CarStatus), nullable=False, default=CarStatus.AT_AUCTION)

    # List page info
    engine = Column(Float, nullable=True)
    has_keys = Column(Boolean, default=False)
    predicted_roi = Column(Float, nullable=True)
    predicted_profit_margin = Column(Float, nullable=True)
    bid = Column(Float, nullable=True)

    # Relationships
    parts = relationship("Part", back_populates="car", cascade="all, delete-orphan")
    photos = relationship("Photo", back_populates="car", cascade="all, delete-orphan")


class Part(Base):
    __tablename__ = "parts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    value = Column(Float, nullable=True)

    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"))
    car = relationship("Car", back_populates="parts")


class Photo(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False)
    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"))

    car = relationship("Car", back_populates="photos")
