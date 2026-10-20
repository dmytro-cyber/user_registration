"""Recoverable source failures in the expired-auction workflow."""


class ExpiredVehicleSourceError(Exception):
    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.stage = stage
