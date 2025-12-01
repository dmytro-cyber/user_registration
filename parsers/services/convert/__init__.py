import os

TASKS_MODE = os.getenv("API_SOURCE")

if TASKS_MODE == "CIA":
    from .vehicle_copart_iaai_api import *
else:
    from .vehicle import *
