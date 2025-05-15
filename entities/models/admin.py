from sqlalchemy import Column, Integer, String, DateTime, Float, func
from sqlalchemy.orm import validates
from models import Base


class FilterModel(Base):
    __tablename__ = "filters"

    id = Column(Integer, primary_key=True, index=True)
    make = Column(String, nullable=True)
    model = Column(String, nullable=True)
    year_from = Column(Integer, nullable=True)
    year_to = Column(Integer, nullable=True)
    odometer_min = Column(Integer, nullable=True)
    odometer_max = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class ROIModel(Base):
    __tablename__ = "roi"

    id = Column(Integer, primary_key=True, index=True)
    roi = Column(Float, nullable=False)
    profit_margin = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=True, default=func.now())

    @validates("roi")
    def validate_and_set_profit_margin(self, key, value):
        if value is not None:
            # Calculate profit_margin based on: PM = 100 - 10000 / (ROI + 100)
            self.profit_margin = round(100 - (10000 / (value + 100)) if value + 100 != 0 else 0, 2)
        return value
