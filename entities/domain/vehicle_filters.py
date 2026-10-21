"""Internal results of vehicle filter workflows."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FilterDeletionResult:
    archived_removed: int
    active_marked_irrelevant: int
