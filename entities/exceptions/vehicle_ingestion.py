class VehicleBatchImportError(Exception):
    """Import or publication failed and the session rollback completed."""


class VehicleUpsertError(Exception):
    """A manual vehicle upsert failed and was rolled back."""
