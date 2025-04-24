from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
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
