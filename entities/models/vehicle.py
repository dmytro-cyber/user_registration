from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func
from models import Base
import enum
from models.user import user_likes


class RecommendationStatus(enum.Enum):
    RECOMMENDED = "recommended"
    NOT_RECOMMENDED = "not_recommended"


class CarStatus(enum.Enum):
    NEW = "New"
    TO_BID = "To Bid"
    BIDDING = "Bidding"
    WON = "Won"
    FAILED = "Failed"
    DELETED_FROM_BIDDING_HUB = "Deleted from Bidding Hub"


class CarModel(Base):
    __tablename__ = "cars"

    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    vehicle = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    make = Column(String, nullable=True)
    model = Column(String, nullable=True)
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
    current_bid = Column(Float, nullable=True)
    actual_bid = Column(Float, nullable=True)
    price_sold = Column(Float, nullable=True)
    suggested_bid = Column(Float, nullable=True)
    avg_market_price = Column(Integer, nullable=True)

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
    car_status = Column(Enum(CarStatus), nullable=False, default=CarStatus.NEW)

    # List page info
    engine = Column(Float, nullable=True)
    has_keys = Column(Boolean, default=False)
    predicted_roi = Column(Float, nullable=True)
    predicted_profit_margin = Column(Float, nullable=True)
    predicted_profit_margin_percent = Column(Float, nullable=True)
    predicted_total_investments = Column(Float, nullable=True)

    # Detail page info
    engine_cylinder = Column(Integer, nullable=True)
    drive_type = Column(String, nullable=True)
    interior_color = Column(String, nullable=True)
    exterior_color = Column(String, nullable=True)
    body_style = Column(String, nullable=True)
    style_id = Column(Integer, nullable=True)
    transmision = Column(String, nullable=True)
    vehicle_type = Column(String, nullable=True)
    link = Column(String, nullable=True)

    inventory = relationship(
        "CarInventoryModel",
        back_populates="car",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Relationships
    auto_checks = relationship("AutoCheckModel", back_populates="car")
    parts = relationship("PartModel", back_populates="car", cascade="all, delete-orphan")
    photos = relationship(
        "PhotoModel",
        primaryjoin="and_(CarModel.id == PhotoModel.car_id, PhotoModel.is_hd == False)",
        back_populates="car_low_res",
        cascade="all, delete-orphan",
    )
    photos_hd = relationship(
        "PhotoModel",
        primaryjoin="and_(CarModel.id == PhotoModel.car_id, PhotoModel.is_hd == True)",
        back_populates="car_high_res",
        cascade="all, delete-orphan",
    )
    bidding_hub_history = relationship("HistoryModel", back_populates="car", cascade="all, delete-orphan")
    condition_assessments = relationship(
        "ConditionAssessmentModel", back_populates="car", cascade="all, delete-orphan"
    )
    sales_history = relationship("CarSaleHistoryModel", back_populates="car", cascade="all, delete-orphan")
    liked_by = relationship("UserModel", secondary=user_likes, back_populates="liked_cars")

    @property
    def engine_and_cylinder(self) -> str:
        return f"{self.engine} / {self.engine_cylinder}"


class HistoryModel(Base):
    __tablename__ = "history"

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"))
    created_at = Column(DateTime, nullable=False, default=func.now())
    action = Column(String, nullable=False)  # "Added", "Deleted", "Updated"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"), nullable=True)
    car_inventory_id = Column(Integer, ForeignKey("car_inventory.id", ondelete="CASCADE"), nullable=True)
    part_inventory_id = Column(Integer, ForeignKey("part_inventory.id", ondelete="CASCADE"), nullable=True)
    comment = Column(String, nullable=True)

    user = relationship("UserModel", back_populates="history")
    car = relationship("CarModel", back_populates="bidding_hub_history")
    car_inventory = relationship("CarInventoryModel", back_populates="history")
    part_inventory = relationship("PartInventoryModel", back_populates="history")


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

    car_low_res = relationship("CarModel", back_populates="photos")
    car_high_res = relationship("CarModel", back_populates="photos_hd")


class CarSaleHistoryModel(Base):
    __tablename__ = "car_sale_history"

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"))
    date = Column(DateTime, nullable=True)
    source = Column(String, nullable=True)
    lot_number = Column(Integer, nullable=True)
    final_bid = Column(Integer, nullable=True)
    status = Column(String, nullable=True)  # "Sold" or "No Sale"

    car = relationship("CarModel", back_populates="sales_history")


class ConditionAssessmentModel(Base):
    __tablename__ = "condition_assessments"

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"))
    type_of_damage = Column(String, nullable=True)
    issue_description = Column(String, nullable=True)

    car = relationship("CarModel", back_populates="condition_assessments")


class AutoCheckModel(Base):
    __tablename__ = "auto_checks"

    id = Column(Integer, primary_key=True, index=True)
    car_id = Column(Integer, ForeignKey("cars.id"), nullable=False)
    screenshot_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    car = relationship("CarModel", back_populates="auto_checks")


class CarInventoryInvestmentsType(enum.Enum):
    AUCTION_FEE = "Auction Fee"
    TRANSPORTATION = "Transportation"
    PARTS = "Parts"
    MAINTENANCE = "Maintenance"
    ADDITIONAL_COSTS = "Additional Costs"
    LABOR = "Labor"


class CarInventoryInvestmentsModel(Base):
    __tablename__ = "car_inventory_investments"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False, default=func.now())
    vendor = Column(String, nullable=False)
    description = Column(String, nullable=False)
    cost = Column(Float, nullable=False)
    payment_method = Column(String, nullable=False)
    investment_type = Column(Enum(CarInventoryInvestmentsType), nullable=False)
    car_inventory_id = Column(Integer, ForeignKey("car_inventory.id", ondelete="CASCADE"), nullable=False)

    car_inventory = relationship("CarInventoryModel", back_populates="investments")


class CarInventoryStatus(enum.Enum):
    AWAITING_DELIVERY = "Awaiting Delivery"
    IN_INVENTORY = "In Inventory"
    UNDER_REPAIR = "Under Repair"
    LISTED_OFR_SALE = "Listed for Sale"
    ACTIVE_SALE = "Active Sale"
    SOLD = "Sold"


class CarInventoryModel(Base):
    __tablename__ = "car_inventory"

    id = Column(Integer, primary_key=True, index=True)
    purchase_date = Column(DateTime, nullable=False, default=func.now())
    vehicle = Column(String, nullable=False)
    vin = Column(String, nullable=False)
    stock = Column(String, nullable=False)

    net_profit = Column(Float, nullable=True)

    vehicle_cost = Column(Float, nullable=True)
    parts_cost = Column(Float, nullable=True)
    maintenance = Column(Float, nullable=True)
    auction_fee = Column(Float, nullable=True)
    transportation = Column(Float, nullable=True)
    labor = Column(Float, nullable=True)
    additional_costs = Column(Float, nullable=True)
    car_status = Column(Enum(CarInventoryStatus), nullable=False, default=CarInventoryStatus.AWAITING_DELIVERY)
    car_id = Column(Integer, ForeignKey("cars.id", ondelete="CASCADE"), nullable=False)

    car = relationship("CarModel", back_populates="inventory", single_parent=True)

    investments = relationship(
        "CarInventoryInvestmentsModel", back_populates="car_inventory", cascade="all, delete-orphan"
    )

    history = relationship("HistoryModel", back_populates="car_inventory", cascade="all, delete-orphan")

    @validates("vin")
    def validate_and_set_stock(self, key, value):
        if value is not None:
            self.stock = value[-6:]
        return value

    @property
    def total_investments(self) -> float:
        return (
            (self.vehicle_cost or 0)
            + (self.parts_cost or 0)
            + (self.maintenance or 0)
            + (self.auction_fee or 0)
            + (self.transportation or 0)
            + (self.labor or 0)
            + (self.additional_costs or 0)
        )

    @property
    def roi(self) -> float:
        """Calculates ROI based on the car's average market price and total investments."""
        if not self.car or not self.car.avg_market_price or self.total_investments == 0:
            return 0.0
        return ((self.car.avg_market_price - self.total_investments) / self.total_investments) * 100

    @property
    def profit_margin_percent(self) -> float:
        """Calculates profit margin percentage."""
        if not self.car or not self.car.avg_market_price or self.car.avg_market_price == 0:
            return 0.0
        return (100 - (self.total_investments / self.car.avg_market_price)) * 100


class FeeModel(Base):
    __tablename__ = "fees"

    id = Column(Integer, primary_key=True, index=True)
    auction = Column(String, nullable=False)
    fee_type = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    price_from = Column(Float, nullable=True)
    price_to = Column(Float, nullable=True)

    @validates("amount")
    def validate_amount(self, key, value):
        if value < 0:
            raise ValueError("Fee amount cannot be negative")
        return value


class PartInventoryStatus(enum.Enum):
    PENDING_TO_ORDER = "Pending to Order"
    ORDERED = "Ordered"
    PENDING_TO_ARRIVED = "Pending to Arrived"
    PAID = "Paid"
    CANCELLED = "Cancelled"
    RECEIVED = "Received"
    PENDING_FOR_RETURN = "Pending for Return"
    SWAP = "Swap"
    RETURNED = "Returned"
    CANT_RETURN = "Can't Return"
    REFUND_REQUEST = "Refund Request"
    REFUNDED = "Refunded"


class PartInventoryModel(Base):
    __tablename__ = "part_inventory"

    id = Column(Integer, primary_key=True, index=True)
    vehicle = Column(String, nullable=False)
    part_description = Column(String, nullable=False)
    supplier = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    part_status = Column(Enum(PartInventoryStatus), nullable=False, default=PartInventoryStatus.PENDING_TO_ORDER)
    comment = Column(String, nullable=True)

    invoices = relationship(
        "InvoiceModel", back_populates="part_inventory", cascade="all, delete-orphan"
    )  # Один до багатьох
    history = relationship("HistoryModel", back_populates="part_inventory", cascade="all, delete-orphan")

    @validates("price")
    def validate_cost_per_unit(self, key, value):
        if value < 0:
            raise ValueError("Cost per unit cannot be negative")
        return value


class InvoiceModel(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    part_inventory_id = Column(
        Integer, ForeignKey("part_inventory.id", ondelete="CASCADE"), nullable=False
    )  # Виправлено ForeignKey
    file_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    part_inventory = relationship("PartInventoryModel", back_populates="invoices")
