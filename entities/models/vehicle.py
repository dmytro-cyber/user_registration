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


class CarModel(Base):
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

    # Detail page info
    engine_cylinder = Column(Integer, nullable=True)
    drive_type = Column(String, nullable=True)
    interior_color = Column(String, nullable=True)
    exterior_color = Column(String, nullable=True)
    body_style = Column(String, nullable=True)
    style_id = Column(Integer, nullable=True)
    transmision = Column(String, nullable=True)
    vehicle_type = Column(String, nullable=True)

    # Relationships
    parts = relationship("PartModel", back_populates="car", cascade="all, delete-orphan")
    photos = relationship(
        "PhotoModel",
        primaryjoin="and_(CarModel.id == PhotoModel.car_id, PhotoModel.is_hd == False)",
        back_populates="car",
        cascade="all, delete-orphan"
    )
    photos_hd = relationship(
        "PhotoModel",
        primaryjoin="and_(CarModel.id == PhotoModel.car_id, PhotoModel.is_hd == True)",
        back_populates="car",
        cascade="all, delete-orphan"
    )
    condition_assessment = relationship("ConditionAssessmentModel", back_populates="car", cascade="all, delete-orphan")
    sales_history = relationship("CarSaleHistoryModel", back_populates="car", cascade="all, delete-orphan")

    @property
    def engine_and_cylinder(self) -> str:
        return f"{self.engine} / {self.engine_cylinder}"


class PartModel(Base):
    __tablename__ = "parts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    value = Column(Float, nullable=True)

    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"))
    car = relationship("CarModel", back_populates="parts")


class PhotoModel(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False)
    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"))
    is_hd = Column(Boolean, default=False, nullable=False)

    car = relationship("CarModel", back_populates=["photos", "photos_hd"])


class CarSaleHistoryModel(Base):
    __tablename__ = "car_sale_history"

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"))
    date = Column(DateTime, nullable=False)
    source = Column(String, nullable=False)
    lot_number = Column(Integer, nullable=False)
    final_bid = Column(Integer, nullable=True)
    status = Column(String, nullable=True)  # "Sold" or "No Sale"

    car = relationship("CarModel", back_populates="sales_history")


class ConditionAssessmentModel(Base):
    __tablename__ = "condition_assessments"

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"))
    part_name = Column(String, nullable=False)
    issue_description = Column(String, nullable=True)

    car = relationship("CarModel", back_populates="condition_assessment")
