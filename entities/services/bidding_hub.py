"""Application scenarios shared independently of their HTTP entrypoints."""

from sqlalchemy.ext.asyncio import AsyncSession

from crud.bidding_hub import (
    add_hub_removal_history,
    get_bid_fees,
    get_bidding_hub_vehicles,
    get_vehicle_for_bid_update,
    get_vehicle_history,
    mark_vehicle_removed_from_hub,
)
from domain.bidding import calculate_auction_fee, calculate_profitability
from exceptions.bidding import (
    BiddingHistoryNotFoundError,
    BidVehicleNotFoundError,
    EmptyBiddingHubError,
    InvalidActualBidError,
)
from models.vehicle import HistoryModel
from schemas.user import UserResponseSchema
from schemas.vehicle import (
    BiddingHubHistoryListResponseSchema,
    BiddingHubHistorySchema,
    CarBiddinHubListResponseSchema,
    CarBiddinHubResponseSchema,
)


async def update_actual_bid(
    db: AsyncSession,
    *,
    car_id: int,
    actual_bid: float | None,
    user_id: int,
    comment: str | None,
) -> None:
    """Update bid, financials and history in the request's existing transaction.

    The vehicle is locked before business validation, preserving error precedence.
    A successful call commits; unexpected failures attempt rollback without masking
    the original error. Expected rejections leave cleanup to the session owner.
    """
    try:
        vehicle = await get_vehicle_for_bid_update(db, car_id)
        if not vehicle:
            raise BidVehicleNotFoundError
        if actual_bid is None or actual_bid < 0:
            raise InvalidActualBidError

        fees = await get_bid_fees(db, vehicle.auction, actual_bid)
        bid_amount = float(actual_bid)
        vehicle.auction_fee = calculate_auction_fee(bid_amount, ((fee.amount, fee.percent) for fee in fees))
        total_investments = bid_amount + vehicle.sum_of_investments
        vehicle.profit_margin, vehicle.roi = calculate_profitability(vehicle.avg_market_price, total_investments)

        db.add(
            HistoryModel(
                car_id=car_id,
                action=f"Updated actual bid from {vehicle.actual_bid} to {actual_bid}",
                user_id=user_id,
                comment=comment,
            )
        )
        vehicle.actual_bid = actual_bid
        db.add(vehicle)
        await db.commit()
    except (BidVehicleNotFoundError, InvalidActualBidError):
        raise
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        raise


async def remove_vehicle_from_hub(db: AsyncSession, *, car_id: int, user_id: int) -> None:
    try:
        vehicle = await get_vehicle_for_bid_update(db, car_id)
        if vehicle is None:
            raise BidVehicleNotFoundError()
        await mark_vehicle_removed_from_hub(db, vehicle)
        add_hub_removal_history(db, car_id, user_id)
        await db.commit()
    except BidVehicleNotFoundError:
        raise
    except Exception:
        await db.rollback()
        raise


async def get_vehicle_bidding_history(db: AsyncSession, *, car_id: int) -> BiddingHubHistoryListResponseSchema:
    """Read history with users and roles; the session owner controls its transaction."""
    history_list = await get_vehicle_history(db, car_id)
    if not history_list:
        raise BiddingHistoryNotFoundError()
    return BiddingHubHistoryListResponseSchema(
        history=[
            BiddingHubHistorySchema(
                id=item.id,
                action=item.action,
                user=(
                    UserResponseSchema(
                        email=item.user.email,
                        first_name=item.user.first_name,
                        last_name=item.user.last_name,
                        phone_number=item.user.phone_number,
                        date_of_birth=item.user.date_of_birth,
                        role=item.user.role.name if item.user.role else None,
                    )
                    if item.user
                    else None
                ),
                comment=item.comment,
                created_at=item.created_at,
            )
            for item in history_list
        ]
    )


async def list_bidding_hub(db: AsyncSession, *, page: int, page_size: int,
                           sort_by: str, sort_order: str) -> CarBiddinHubListResponseSchema:
    vehicles, total_count, total_pages = await get_bidding_hub_vehicles(
        db, page=page, page_size=page_size, sort_by=sort_by, sort_order=sort_order,
    )
    if not vehicles:
        raise EmptyBiddingHubError()
    return CarBiddinHubListResponseSchema(
        vehicles=[CarBiddinHubResponseSchema.from_orm(vehicle) for vehicle in vehicles],
        total_count=total_count,
        total_pages=total_pages,
    )
