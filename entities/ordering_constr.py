from sqlalchemy import asc, desc, cast, Date
from models.vehicle import CarModel


ORDERING_MAP = {
    "created_at_asc": cast(CarModel.created_at, Date).asc(),
    "created_at_desc": cast(CarModel.created_at, Date).desc(),
    "current_bid_asc": asc(CarModel.current_bid),
    "current_bid_desc": desc(CarModel.current_bid),
    "recommendation_status_asc": asc(CarModel.recommendation_status),
    "recommendation_status_desc": desc(CarModel.recommendation_status),
    "auction_date_asc": cast(CarModel.date, Date).asc(),
    "auction_date_desc": cast(CarModel.date, Date).desc(),
}
