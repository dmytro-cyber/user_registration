"""Rules for deciding whether a manual vehicle upsert needs enrichment."""

from typing import Any, Mapping

PARSER_RELEVANT_FIELDS = frozenset(
    {
        "vehicle",
        "engine_title",
        "mileage",
        "make",
        "model",
        "year",
        "transmision",
    }
)


def needs_enrichment(*, checked: bool, previous: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    return not checked or any(
        incoming.get(field) is not None and incoming[field] != previous.get(field) for field in PARSER_RELEVANT_FIELDS
    )
