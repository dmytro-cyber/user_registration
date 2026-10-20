class BidVehicleNotFoundError(Exception):
    """The vehicle targeted by a bid update does not exist."""


class InvalidActualBidError(Exception):
    """An actual bid must be present and non-negative."""
