"""Application scenarios shared independently of their HTTP entrypoints."""

from sqlalchemy.ext.asyncio import AsyncSession

from crud.bidding_hub import get_bid_fees, get_vehicle_for_bid_update
from domain.bidding import calculate_auction_fee, calculate_profitability
from exceptions.bidding import BidVehicleNotFoundError, InvalidActualBidError
from models.vehicle import HistoryModel


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

        db.add(HistoryModel(
            car_id=car_id,
            action=f"Updated actual bid from {vehicle.actual_bid} to {actual_bid}",
            user_id=user_id,
            comment=comment,
        ))
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
