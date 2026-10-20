"""Application workflow for vehicle enrichment; Celery owns task retries."""

import logging
from collections.abc import Callable

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from crud.enrichment_pricing import load_default_roi as _load_default_roi
from crud.enrichment_pricing import load_enrichment_fees as _load_fees
from crud.vehicle_enrichment import load_enrichment_snapshot
from domain.enrichment_planning import EnrichmentCompletion, calculate_roi_projection, prepare_enrichment
from domain.enrichment_planning import apply_enrichment_fees as _apply_fees
from domain.vehicle_enrichment import VehicleEnrichmentReportStorage, VehicleEnrichmentSource, is_lock_conflict_error
from domain.vehicle_ingestion import EnrichmentTaskPayload
from exceptions.vehicle_enrichment import EnrichmentLockConflict, EnrichmentParserError
from services.vehicle_enrichment import complete_vehicle_enrichment, record_vehicle_parser_failure

logger = logging.getLogger(__name__)


def enrich_vehicle(
    sessions: Callable[[], Session],
    request: EnrichmentTaskPayload,
    source: VehicleEnrichmentSource,
    report_storage_factory: Callable[[], VehicleEnrichmentReportStorage],
) -> dict[str, str]:
    vin = request["vin"]
    mileage = request["mileage"]
    logger.info("parse_and_update_car started for VIN=%s", vin)

    # ---------------------------------------------------------
    # 1. Read current car state WITHOUT LOCK
    # ---------------------------------------------------------
    with sessions() as db:
        snapshot = load_enrichment_snapshot(db, vin)
        if snapshot is None:
            raise ValueError(f"Car with VIN {vin} not found")
        business_attempts = snapshot.business_attempts

    # ---------------------------------------------------------
    # 2. Fetch sales history WITHOUT LOCK
    # ---------------------------------------------------------
    sale_history_rows = []
    sales_history_reason_to_add = None

    if business_attempts == 0:
        try:
            hist_result = source.fetch_history(vin)
            if hist_result is None:
                logger.warning("Sales history response is None for vin=%s", vin)
                sale_history_data = []
            else:
                sale_history_data = hist_result.sales_history or []

            logger.info(
                "Sales history fetched for VIN=%s, items=%s",
                vin,
                len(sale_history_data),
            )

            if sale_history_data:
                if len(sale_history_data) >= 4:
                    sales_history_reason_to_add = f"sales at auction in the last 3 years: {len(sale_history_data)};"

                for h in sale_history_data:
                    history_payload = h.model_dump() if hasattr(h, "model_dump") else h.dict()
                    if not history_payload.get("source"):
                        history_payload["source"] = "Unknown"
                    sale_history_rows.append(history_payload)

        except Exception as e:
            logger.warning("Sales history fetch failed for %s: %s", vin, e)

    # ---------------------------------------------------------
    # 3. Main parser call WITHOUT LOCK
    # ---------------------------------------------------------
    try:
        data = source.parse(request)
    except EnrichmentParserError as exc:
        logger.warning("Parser error for VIN=%s: %s", vin, exc)
        with sessions() as db:
            return record_vehicle_parser_failure(db, vin, exc.reason)

    # ---------------------------------------------------------
    # 4. Prepare all computed values WITHOUT LOCK
    # ---------------------------------------------------------
    preparation = prepare_enrichment(data, snapshot.market_price, snapshot.market_sources)
    market = preparation.market

    with sessions() as db:
        default_roi = _load_default_roi(db)

        projection = calculate_roi_projection(market.calculation_price, default_roi)

        current_auction = snapshot.auction
        fees = _load_fees(db, current_auction, float(projection.total_investments or 0.0))
        auction_fee = _apply_fees(float(projection.total_investments or 0.0), fees)

    # ---------------------------------------------------------
    # 5. Upload HTML to S3 WITHOUT LOCK
    # ---------------------------------------------------------
    screenshot_url = None
    html_data = data.get("html_data")

    if html_data:
        try:
            report_storage = report_storage_factory()
            screenshot_url = report_storage.save(vin, html_data)
        except Exception as e:
            logger.warning("S3 upload failed for VIN=%s: %s", vin, e)

    # ---------------------------------------------------------
    # 6. Final SHORT locked DB write
    # ---------------------------------------------------------
    completion = EnrichmentCompletion(
        preparation=preparation,
        projection=projection,
        auction_fee=auction_fee,
        expected_mileage=mileage,
        business_attempts=business_attempts,
        sales_history_rows=sale_history_rows,
        sales_history_reason=sales_history_reason_to_add,
        screenshot_url=screenshot_url,
    )
    with sessions() as db:
        try:
            return complete_vehicle_enrichment(db, vin, completion)
        except OperationalError as exc:
            if is_lock_conflict_error(exc):
                raise EnrichmentLockConflict(exc) from exc
            logger.error("OperationalError for VIN=%s: %s", vin, exc, exc_info=True)
            raise
