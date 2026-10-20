class VehicleSourceUnavailable(Exception):
    """The source failed before returning a vehicle payload."""


class InvalidVehicleSourceData(Exception):
    """The source payload cannot be validated as vehicle data."""


class SavedVehicleNotFound(Exception):
    """Persistence did not leave a readable vehicle for the requested VIN."""
