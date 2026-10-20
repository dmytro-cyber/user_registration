"""Vehicle recommendation policies, independent of persistence and API schemas.

The import and manual-upsert policies intentionally retain their existing
differences in normalization, bid limits and reason formatting.
"""

from dataclasses import dataclass

SEVERE_DAMAGE_ISSUES = frozenset(
    {
        "Rejected Repair",
        "Burn Engine",
        "Mechanical",
        "Replaced Vin",
        "Burn",
        "Undercarriage",
        "Water/Flood",
        "Burn Interior",
        "Rollover",
    }
)
IMPORT_ALLOWED_FUELS = frozenset({"Gasoline", "Flexible Fuel", "Unknown"})
MANUAL_ALLOWED_FUELS = frozenset({"gasoline", "flexible fuel", "unknown", "gas"})
BID_REASON = "BID_GT_SUGGESTED"
REFRESH_BID_REASON = "suggested bid < current bid;"


@dataclass(frozen=True)
class RecommendationState:
    recommended: bool
    reasons: str | None = None
    manually_set: bool = False


@dataclass(frozen=True)
class RecommendationFacts:
    fuel_type: str | None = None
    transmission: str | None = None
    damage_issues: tuple[str | None, ...] = ()
    current_bid: float | None = None
    suggested_bid: float | None = None
    actual_bid: float | None = None
    sales_count: int = 0


def normalize_manual_value(value: object) -> str:
    return str(value).replace("\u00a0", " ").replace("\u200b", "").strip().lower()


def _append_once(reasons: str | None, reason: str) -> str:
    if not reasons:
        return f"{reason};"
    return reasons if f"{reason};" in reasons else reasons + f"{reason};"


def _new_reason(value: object, previous: object, manually_set: bool) -> bool:
    return not manually_set or value != previous


def recommend_after_bid_sync(
    state: RecommendationState,
    previous: RecommendationFacts,
    current_bid: float | None,
) -> RecommendationState:
    limit = previous.actual_bid if previous.actual_bid is not None else previous.suggested_bid
    if current_bid is not None and current_bid != previous.current_bid and limit is not None and current_bid > limit:
        return RecommendationState(False, _append_once(state.reasons, BID_REASON))
    return state


def recommend_after_sales_history(state: RecommendationState, count: int) -> RecommendationState:
    if count < 4:
        return state
    reason = f"sales at auction in the last 3 years: {count};"
    return RecommendationState(False, (state.reasons or "") + reason, state.manually_set)


def recommend_after_bid_refresh(
    state: RecommendationState,
    previous: RecommendationFacts,
    current_bid: int,
) -> RecommendationState:
    limit = previous.actual_bid if previous.actual_bid is not None else previous.suggested_bid
    if limit is None:
        return state
    if current_bid > limit and (not state.manually_set or current_bid != previous.current_bid):
        reasons = state.reasons or ""
        if REFRESH_BID_REASON not in reasons:
            reasons = (reasons + REFRESH_BID_REASON).strip()
        return RecommendationState(False, reasons, False)
    if current_bid <= limit and not state.manually_set:
        reasons = state.reasons
        if reasons:
            reasons = reasons.replace(REFRESH_BID_REASON, "")
        return RecommendationState(state.recommended if reasons else True, reasons, False)
    return state


def recommend_for_manual_upsert(
    state: RecommendationState,
    previous: RecommendationFacts | None,
    incoming: RecommendationFacts,
) -> RecommendationState:
    old = previous or RecommendationFacts()
    fuel = incoming.fuel_type if incoming.fuel_type is not None else old.fuel_type
    transmission = incoming.transmission if incoming.transmission is not None else old.transmission
    reasons, recommended = state.reasons, state.recommended
    if fuel and fuel.strip().lower() not in MANUAL_ALLOWED_FUELS:
        reasons, recommended = _append_once(reasons, fuel), False
    if transmission and transmission.strip().lower() != "automatic":
        reasons, recommended = _append_once(reasons, transmission), False
    for issue in incoming.damage_issues:
        if issue in SEVERE_DAMAGE_ISSUES:
            reasons, recommended = _append_once(reasons, issue), False

    override = (
        bool(
            incoming.fuel_type
            and incoming.fuel_type not in MANUAL_ALLOWED_FUELS
            and incoming.fuel_type != normalize_manual_value(old.fuel_type)
        )
        or bool(
            incoming.transmission
            and incoming.transmission != "automatic"
            and incoming.transmission != normalize_manual_value(old.transmission)
        )
        or any(issue in SEVERE_DAMAGE_ISSUES and issue not in old.damage_issues for issue in incoming.damage_issues)
    )
    if previous is not None and state.manually_set and not override:
        return state
    return RecommendationState(
        recommended, reasons, False if previous is not None and override else state.manually_set
    )


def recommend_for_snapshot(
    incoming: RecommendationFacts,
    *,
    previous: RecommendationFacts | None = None,
    state: RecommendationState | None = None,
) -> RecommendationState:
    old = previous or RecommendationFacts()
    manual = bool(state and state.manually_set)
    reasons = state.reasons if manual else None
    override = False

    # New snapshot rows historically record missing values as "None" reasons.
    if (previous is None or incoming.fuel_type is not None) and incoming.fuel_type not in IMPORT_ALLOWED_FUELS:
        if _new_reason(incoming.fuel_type, old.fuel_type, manual):
            reasons = (reasons or "") + f"{incoming.fuel_type};"
            override = True
    if (previous is None or incoming.transmission is not None) and incoming.transmission != "Automatic":
        if _new_reason(incoming.transmission, old.transmission, manual):
            reasons = (reasons or "") + f"{incoming.transmission};"
            override = True
    for issue in incoming.damage_issues:
        if issue in SEVERE_DAMAGE_ISSUES and (not manual or issue not in old.damage_issues):
            reasons = (reasons or "") + f"{issue};"
            override = True
    limit = incoming.suggested_bid if incoming.suggested_bid is not None else old.suggested_bid
    if (
        previous is not None
        and incoming.current_bid is not None
        and limit is not None
        and incoming.current_bid > limit
    ):
        if _new_reason(incoming.current_bid, old.current_bid, manual):
            reasons = (reasons or "") + f"{BID_REASON};"
            override = True
    if incoming.sales_count >= 4:
        reasons = (reasons or "") + f"sales at auction in the last 3 years: {incoming.sales_count};"
        override = True
    if state is not None and manual and not override:
        return state
    return RecommendationState(not bool(reasons), reasons)


def recommend_for_bulk(
    incoming: RecommendationFacts,
    *,
    previous: RecommendationFacts | None = None,
    state: RecommendationState | None = None,
    manually_upserted: bool = False,
) -> RecommendationState:
    old = previous or RecommendationFacts()
    if manually_upserted:
        assert state is not None and previous is not None
        decision = recommend_after_bid_sync(state, old, incoming.current_bid)
        if decision is state:
            return state
        # Bulk canonicalizes the existing separators when a new bid reason is added.
        reasons = [reason for reason in (state.reasons or "").split(";") if reason]
        if BID_REASON not in reasons:
            reasons.append(BID_REASON)
        return RecommendationState(False, ";".join(reasons) + ";")
    manual = bool(state and state.manually_set)
    reasons: list[str] = []

    def append(reason: str | None) -> None:
        if reason and reason not in reasons:
            reasons.append(reason)

    if incoming.fuel_type and incoming.fuel_type not in IMPORT_ALLOWED_FUELS:
        if _new_reason(incoming.fuel_type, old.fuel_type, manual):
            append(incoming.fuel_type)
    if incoming.transmission and incoming.transmission != "Automatic":
        if _new_reason(incoming.transmission, old.transmission, manual):
            append(incoming.transmission)
    for issue in incoming.damage_issues:
        if issue in SEVERE_DAMAGE_ISSUES and (not manual or issue not in old.damage_issues):
            append(issue)
    limit = old.actual_bid if old.actual_bid is not None else old.suggested_bid
    if (
        previous is not None
        and incoming.current_bid is not None
        and limit is not None
        and incoming.current_bid > limit
    ):
        if _new_reason(incoming.current_bid, old.current_bid, manual):
            append(BID_REASON)
    if state is not None and manual:
        if not reasons:
            return state
        combined = [reason for reason in (state.reasons or "").split(";") if reason]
        for reason in reasons:
            if reason not in combined:
                combined.append(reason)
        reasons = combined
    return RecommendationState(not bool(reasons), ";".join(reasons) + ";" if reasons else None)
