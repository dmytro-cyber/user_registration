from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.sql.expression import desc
import re
from datetime import datetime
from schemas.analytic import AuctionFilters


# Helper function to extract state and city
def parse_location(location: str) -> Tuple[Optional[str], Optional[str]]:
    if not location:
        return None, None
    if match := re.match(r"^(?:[A-Z]{2}\s*-)?(.+?)(?:\s*\(([A-Z]{2})\))?$", location):
        city_part, state_part = match.groups()
        state_code = state_part if state_part else (location[:2] if re.match(r"^[A-Z]{2}\s*-", location) else None)
        city = city_part.strip() if city_part else None
        return state_code, city
    return None, None

# Dynamic query builder
async def build_top_sellers_query(db: AsyncSession, filters: AuctionFilters):
    query = select("seller".label("Seller Name"), func.count().label("Lots")).\
        join("car", "car_sale_history.car_id" == "car.id").\
        join("condition_assessments", "car.id" == "condition_assessments.car_id").\
        where("car_sale_history.status" == "Sold").\
        where("car_sale_history.final_bid" != None).\
        where("car_sale_history.seller" != None).\
        group_by("car_sale_history.seller").\
        order_by(desc("Lots")).\
        limit(10)

    # Apply location parsing and filters
    if filters.state_codes or filters.cities:
        subquery = select("car.id", *[
            func.case(
                (func.regexp_match("car.location", r"\([A-Z]{2}\)", "i"), func.regexp_replace("car.location", r".*\(([A-Z]{2})\).*", r"\1")),
                (func.regexp_match("car.location", r"^[A-Z]{2}\s*-", "i"), func.left("car.location", 2)),
                else_=None
            ).label("state_code"),
            func.case(
                (func.regexp_match("car.location", r"\([A-Z]{2}\)", "i"), func.trim(func.regexp_replace("car.location", r"\s*\([A-Z]{2}\)", ""))),
                (func.regexp_match("car.location", r"^[A-Z]{2}\s*-", "i"), func.trim(func.split_part("car.location", "-", 2))),
                else_=None
            ).label("city")
        ]).subquery()

        query = query.join(subquery, subquery.c.id == "car.id")

        if filters.state_codes:
            query = query.where(subquery.c.state_code.in_(filters.state_codes))
        if filters.cities:
            query = query.where(subquery.c.city.in_(filters.cities))

    # Apply other filters
    if filters.auctions:
        query = query.where("car.auction".in_(filters.auctions))
    if filters.mileage_start and filters.mileage_end:
        query = query.where("car.mileage".between(filters.mileage_start, filters.mileage_end))
    if filters.owners_start and filters.owners_end:
        query = query.where("condition_assessments.owners".between(filters.owners_start, filters.owners_end))
    if filters.accident_start and filters.accident_end:
        query = query.where("condition_assessments.accident_count".between(filters.accident_start, filters.accident_end))
    if filters.year_start and filters.year_end:
        query = query.where("car.year".between(filters.year_start, filters.year_end))
    if filters.vehicle_condition:
        query = query.where("condition_assessments.issue_description".in_(filters.vehicle_condition))
    if filters.vehicle_types:
        query = query.where("car.vehicle_type".in_(filters.vehicle_types))
    if filters.make:
        query = query.where("car.make" == filters.make)
    if filters.model:
        query = query.where("car.model" == filters.model)
    if filters.predicted_roi_start and filters.predicted_roi_end:
        query = query.where("condition_assessments.predicted_roi".between(filters.predicted_roi_start, filters.predicted_roi_end))
    if filters.predicted_profit_margin_start and filters.predicted_profit_margin_end:
        query = query.where("condition_assessments.predicted_profit_margin".between(filters.predicted_profit_margin_start, filters.predicted_profit_margin_end))
    if filters.engine_type:
        query = query.where("car.engine".in_(filters.engine_type))
    if filters.transmission:
        query = query.where("car.transmission".in_(filters.transmission))
    if filters.drive_train:
        query = query.where("car.drive_type".in_(filters.drive_train))
    if filters.cylinder:
        query = query.where("car.engine_cylinder".in_(filters.cylinder))
    if filters.auction_names:
        query = query.where("car.auction_name".in_(filters.auction_names))
    if filters.body_style:
        query = query.where("car.body_style".in_(filters.body_style))
    if filters.sale_start and filters.sale_end:
        start_date = datetime.fromisoformat(filters.sale_start).date()
        end_date = datetime.fromisoformat(filters.sale_end).date()
        query = query.where("car_sale_history.date".between(start_date, end_date))

    return query