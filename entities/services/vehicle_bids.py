"""Current-bid refresh transaction, independent of Celery and HTTP transport."""

import logging

from sqlalchemy.orm import Session

from crud.vehicle_bids import find_vehicle_for_bid_update, load_bid_targets
from domain.bid_refresh import BidRefreshResult, CurrentBidSource, profit_from_current_bid, read_auction_bid
from domain.vehicle_recommendations import RecommendationFacts, recommend_after_bid_refresh
from services.vehicle_recommendations import apply_recommendation, recommendation_state

logger = logging.getLogger(__name__)


def refresh_vehicle_bids(db: Session, source: CurrentBidSource) -> BidRefreshResult:
    updated = 0
    try:
        targets = load_bid_targets(db)
        if not targets:
            return {"status": "success", "updated_cars": 0}
        payload = source.fetch(targets)
        bids = payload.get("bids", []) if isinstance(payload, dict) else payload
        for item in bids:
            try:
                bid = read_auction_bid(item)
                if bid is None:
                    continue
                car = find_vehicle_for_bid_update(db, bid.lot, bid.auction)
                if car is None:
                    continue
                previous = RecommendationFacts(
                    current_bid=car.current_bid,
                    actual_bid=car.actual_bid,
                    suggested_bid=car.suggested_bid,
                )
                try:
                    car.current_bid = int(float(bid.amount))
                except (ValueError, TypeError):
                    continue
                apply_recommendation(
                    car, recommend_after_bid_refresh(recommendation_state(car), previous, car.current_bid)
                )
                profit = profit_from_current_bid(car.sum_of_investments, car.current_bid, car.avg_market_price)
                if profit is not None:
                    car.predicted_profit_margin = profit.margin
                    car.predicted_roi = profit.roi
                updated += 1
            except Exception as exc:
                logger.info("update_car_bids: skipped lot=%r due to: %s", item.get("lot_id"), exc)
                continue
        db.commit()
        return {"status": "success", "updated_cars": updated}
    except Exception:
        db.rollback()
        logger.exception("update_car_bids failed")
        raise
