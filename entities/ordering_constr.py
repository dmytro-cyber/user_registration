from sqlalchemy import Date, asc, cast, desc, nulls_last

from models.vehicle import CarModel

ORDERING_MAP = {
    "created_at_asc": nulls_last(asc(CarModel.created_at)),
    "created_at_desc": nulls_last(desc(CarModel.created_at)),
    "current_bid_asc": nulls_last(asc(CarModel.current_bid)),
    "current_bid_desc": nulls_last(desc(CarModel.current_bid)),
    "recommendation_status_asc": nulls_last(asc(CarModel.recommendation_status)),
    "recommendation_status_desc": nulls_last(desc(CarModel.recommendation_status)),
    "auction_date_asc": nulls_last(asc(CarModel.date)),
    "auction_date_desc": nulls_last(desc(CarModel.date)),
}
