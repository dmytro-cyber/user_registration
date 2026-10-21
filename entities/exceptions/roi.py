"""Application errors for admin ROI workflows."""


class NegativeROIError(ValueError):
    pass


class ROIRecordNotFound(Exception):
    pass
