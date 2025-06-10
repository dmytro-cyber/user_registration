from sqlalchemy.orm import declarative_base

Base = declarative_base()

from .user import UserModel as UserModel
from .user import UserRoleModel as UserRoleModel
from .user import UserRoleEnum as UserRoleEnum
from .vehicle import HistoryModel as HistoryModel
from .vehicle import PhotoModel as PhotoModel
from .vehicle import CarModel as CarModel
from .vehicle import PartModel as PartModel
from .vehicle import ConditionAssessmentModel as ConditionAssessmentModel
from .vehicle import CarSaleHistoryModel as CarSaleHistoryModel
from .admin import FilterModel as FilterModel
from .admin import ROIModel as ROIModel
from .vehicle import CarInventoryModel as CarInventoryModel
from .vehicle import CarInventoryInvestmentsModel as CarInventoryInvestmentsModel
