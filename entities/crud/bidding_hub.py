"""Persistence operations for bidding scenarios; transaction ownership is external."""

from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.sql import over

from exceptions.bidding import InvalidBiddingHubSortError
from models.user import UserModel
from models.vehicle import CarModel, CarStatus, FeeModel, HistoryModel


async def get_vehicle_for_bid_update(db: AsyncSession, car_id: int) -> CarModel | None:
    result = await db.execute(select(CarModel).where(CarModel.id == car_id).with_for_update())
    return result.scalars().first()


async def get_bid_fees(db: AsyncSession, auction: str, actual_bid: float) -> list[FeeModel]:
    result = await db.execute(
        select(FeeModel).where(
            FeeModel.auction.ilike(auction),
            FeeModel.price_from <= actual_bid,
            FeeModel.price_to >= actual_bid,
        )
    )
    return list(result.scalars().all())


async def mark_vehicle_removed_from_hub(db: AsyncSession, vehicle: CarModel) -> None:
    vehicle.car_status = CarStatus.DELETED_FROM_BIDDING_HUB
    await db.flush()


def add_hub_removal_history(db: AsyncSession, car_id: int, user_id: int) -> None:
    db.add(HistoryModel(car_id=car_id, action="Deleted vehicle from Bidding Hub", user_id=user_id))


async def get_vehicle_history(db: AsyncSession, car_id: int) -> list[HistoryModel]:
    result = await db.execute(
        select(HistoryModel)
        .where(HistoryModel.car_id == car_id)
        .options(selectinload(HistoryModel.user).selectinload(UserModel.role))
        .order_by(HistoryModel.created_at.desc())
    )
    return list(result.scalars().all())


async def get_bidding_hub_vehicles(
    db: AsyncSession,
    page: int,
    page_size: int,
    sort_by: str = "date",
    sort_order: str = "desc",
) -> tuple[list[CarModel], int, int]:
    """Get vehicles in the bidding hub with pagination and sorting, including the last user who made a manipulation."""
    order_func = asc if sort_order.lower() == "asc" else desc

    history_alias = aliased(HistoryModel)
    subquery = select(
        history_alias.id.label("id"),
        history_alias.car_id.label("car_id"),
        history_alias.user_id.label("user_id"),
        over(
            func.row_number(),
            partition_by=history_alias.car_id,
            order_by=history_alias.created_at.desc(),
        ).label("rn"),
    ).subquery()

    query = (
        select(CarModel)
        .outerjoin(subquery, (subquery.c.car_id == CarModel.id) & (subquery.c.rn == 1))
        .options(selectinload(CarModel.bidding_hub_history).selectinload(HistoryModel.user))
    )

    query = query.filter(
        ~CarModel.car_status.in_(
            [
                CarStatus.NEW,
                CarStatus.DELETED_FROM_BIDDING_HUB,
            ]
        )
    )

    if sort_by == "user":
        query = query.join(UserModel, UserModel.id == subquery.c.user_id)
        query = query.order_by(order_func(UserModel.email))
    else:
        sort_field_mapping = {
            "vehicle": CarModel.vehicle,
            "auction": CarModel.auction,
            "location": CarModel.location,
            "date": CarModel.date,
            "lot": CarModel.lot,
            "avg_market_price": CarModel.avg_market_price,
            "predicted_total_investments": CarModel.predicted_total_investments,
            "predicted_profit_margin": CarModel.predicted_profit_margin,
            "predicted_roi": CarModel.predicted_roi,
            "actual_bid": CarModel.actual_bid,
            "status": CarModel.car_status,
            "current_bid": CarModel.current_bid,
            "suggested_bid": CarModel.suggested_bid,
        }
        sort_field = sort_field_mapping.get(sort_by)

        if sort_field:
            query = query.order_by(order_func(sort_field))
        else:
            raise InvalidBiddingHubSortError(sort_by)

    total_count = await db.scalar(select(func.count()).select_from(query.subquery()))
    total_pages = (total_count + page_size - 1) // page_size

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    vehicles = result.scalars().all()

    return vehicles, total_count, total_pages
