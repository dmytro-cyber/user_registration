"""Recoverable failures of the main enrichment parser."""

from sqlalchemy.exc import OperationalError


class EnrichmentParserError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class EnrichmentLockConflict(Exception):
    """Only a lock conflict from the final write qualifies for Celery retry."""

    def __init__(self, error: OperationalError) -> None:
        super().__init__(str(error))
        self.error = error
