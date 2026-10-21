from sqlalchemy import Column, DateTime, Float, Integer, String, func
from sqlalchemy.orm import validates

from domain.roi import profit_margin_for_roi
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
            self.profit_margin = profit_margin_for_roi(value)
        return value
