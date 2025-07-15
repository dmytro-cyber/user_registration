from sqlalchemy import asc, desc, cast, Date
from models.vehicle import CarModel



ORDERING_MAP = {
    "created_at_asc": [asc(CarModel.id), asc(CarModel.created_at)],
    "created_at_desc": [asc(CarModel.id), desc(CarModel.created_at)],
    
    "current_bid_asc": [asc(CarModel.id), asc(CarModel.current_bid)],
    "current_bid_desc": [asc(CarModel.id), desc(CarModel.current_bid)],
    
    "recommendation_status_asc": [asc(CarModel.id), asc(CarModel.recommendation_status)],
    "recommendation_status_desc": [asc(CarModel.id), desc(CarModel.recommendation_status)],
    
    "auction_date_asc": [asc(CarModel.id), asc(CarModel.date)],
    "auction_date_desc": [asc(CarModel.id), desc(CarModel.date)],
}
