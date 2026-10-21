class CostVehicleNotFoundError(Exception):
    """The requested vehicle does not exist."""


class EmptyVehicleCostsError(Exception):
    """No cost fields were provided."""


class VehicleCostsPersistenceError(Exception):
    """Commit or post-commit refresh failed after rollback was attempted."""
