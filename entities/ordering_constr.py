from sqlalchemy import Date, asc, cast, desc

from models.vehicle import CarModel

ORDERING_MAP = {
    "created_at_asc": asc(CarModel.created_at),
    "created_at_desc": desc(CarModel.created_at),
    "current_bid_asc": asc(CarModel.current_bid),
    "current_bid_desc": desc(CarModel.current_bid),
    "recommendation_status_asc": asc(CarModel.recommendation_status),
    "recommendation_status_desc": desc(CarModel.recommendation_status),
    "auction_date_asc": asc(CarModel.date),
    "auction_date_desc": desc(CarModel.date),
}
