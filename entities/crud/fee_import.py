"""Replace one auction's tariffs inside the caller's transaction."""

from collections.abc import Iterable
from dataclasses import asdict

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from domain.fee_import import FeeBand
from models.vehicle import FeeModel


def replace_copart_fees(db: Session, bands: Iterable[FeeBand]) -> None:
    db.execute(delete(FeeModel).where(FeeModel.auction == "copart"))
    for band in bands:
        db.add(FeeModel(auction="copart", **asdict(band)))


async def replace_iaai_fees(db: AsyncSession, bands: Iterable[FeeBand]) -> None:
    await db.execute(delete(FeeModel).where(FeeModel.auction == "iaai"))
    for band in bands:
        db.add(FeeModel(auction="iaai", **asdict(band)))
