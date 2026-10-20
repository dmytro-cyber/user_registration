"""Auction tariff import transactions, independent of Celery and transport."""

import logging
from typing import Literal, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from crud.fee_import import replace_copart_fees, replace_iaai_fees
from domain.fee_import import CopartFeeSource, copart_fee_bands
from domain.iaai_fees import FeeImage, IaaiFeeResponse, IaaiFeeSource, iaai_fee_bands

logger = logging.getLogger(__name__)


class FeeImportResult(TypedDict):
    status: Literal["success"]


def refresh_copart_fees(db: Session, source: CopartFeeSource) -> FeeImportResult:
    try:
        payload = source.fetch()
        fees = payload["fees"]["copart"]["fees"]
        replace_copart_fees(db, copart_fee_bands(fees))
        db.commit()
        return {"status": "success"}
    except Exception:
        db.rollback()
        logger.exception("update_car_fees failed")
        raise


async def refresh_iaai_fees(
    db: AsyncSession,
    source: IaaiFeeSource,
    high_volume: FeeImage,
    internet_bid: FeeImage,
) -> IaaiFeeResponse:
    try:
        response = await source.fetch(high_volume, internet_bid)
        bands = iaai_fee_bands(response.data)
        await replace_iaai_fees(db, bands)
        await db.commit()
        return response
    except Exception:
        await db.rollback()
        logger.exception("IAAI fee import failed")
        raise
