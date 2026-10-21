class BidVehicleNotFoundError(Exception):
    """The vehicle targeted by a bid update does not exist."""


class InvalidActualBidError(Exception):
    """An actual bid must be present and non-negative."""


class BiddingHistoryNotFoundError(Exception):
    """No history records exist for the requested vehicle ID."""


class EmptyBiddingHubError(Exception):
    """The requested page contains no bidding-hub vehicles."""


class InvalidBiddingHubSortError(Exception):
    """The requested bidding-hub sort field is unsupported."""
