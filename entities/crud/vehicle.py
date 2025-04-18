from fastapi import HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from models import CarModel, PhotoModel
from schemas.vehicle import CarCreateSchema
import logging
from db.session import get_db


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def save_vehicle(vehicle_data: CarCreateSchema) -> bool:
    """
    Save a single vehicle and its photos within a transaction.
    
    Args:
        vehicle_data: Pydantic schema with vehicle data and photos.
        db: Database session.
    
    Returns:
        bool: True if saved successfully, False if duplicate VIN.
    
    Raises:
        HTTPException: For non-duplicate VIN database errors or unexpected errors.
    """
    db = await get_db().__anext__()
    try:
        async with db.begin():
            
            vehicle = CarModel(**vehicle_data.dict(exclude={"photos"}))
            db.add(vehicle)
            await db.flush()

            if hasattr(vehicle_data, "photos") and vehicle_data.photos:
                for photo_data in vehicle_data.photos:
                    photo = PhotoModel(url=photo_data.url, car_id=vehicle.id)
                    db.add(photo)
            
            await db.commit()
            
            logger.info(f"Vehicle {vehicle.vin} saved successfully with ID {vehicle.id}.")
            return True

    except IntegrityError as e:
        if "unique constraint" in str(e).lower() and "vin" in str(e).lower():
            return False
        else:
            raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
