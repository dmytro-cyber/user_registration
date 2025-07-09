from datetime import datetime
import logging
import logging.handlers
import os
from utils import update_inventory_financials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc
from sqlalchemy.orm import selectinload
from models.vehicle import (
    CarInventoryModel,
    CarInventoryInvestmentsModel,
    CarInventoryStatus,
    PartInventoryModel,
    HistoryModel,
    InvoiceModel,
)
from core.config import settings
from models.user import UserModel
from core.dependencies import get_s3_storage_client
from schemas.inventory import (
    CarInventoryCreate,
    CarInventoryUpdate,
    CarInventoryInvestmentsCreate,
    CarInventoryInvestmentsUpdate,
    PartInventoryCreate,
    PartInventoryUpdate,
    PartInventoryStatusUpdate,
)

# Configure logging for production environment
logger = logging.getLogger("inventory_crud")
logger.setLevel(logging.DEBUG)  # Set the default logging level

# Define formatter for structured logging
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - [RequestID: %(request_id)s] - [UserID: %(user_id)s] - %(message)s"
)

# Set up console handler for debug output
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

# Add handlers to the logger (only console handler is active)
logger.addHandler(console_handler)


# Custom filter to add context (RequestID, UserID)
class ContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(record, "request_id", "N/A")
        record.user_id = getattr(record, "user_id", "N/A")
        return True


logger.addFilter(ContextFilter())

# --- CarInventory CRUD Operations ---


async def create_car_inventory(
    db: AsyncSession,
    inventory: CarInventoryCreate,
    user_id: str,
    request_id: str = "N/A",
):
    """
    Create a new car inventory entry and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        inventory (CarInventoryCreate): The car inventory data to create.
        user_id (str): The ID of the user making the request.
        request_id (str): The request ID (for logging).

    Returns:
        CarInventoryModel: The created car inventory object.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Creating car inventory for user {user_id}", extra=extra)

    db_inventory = CarInventoryModel(**inventory.dict(exclude_unset=True))
    db.add(db_inventory)
    await db.commit()
    await db.refresh(db_inventory)

    # Create a history record
    history = HistoryModel(
        action="Added",
        user_id=int(user_id),
        car_inventory_id=db_inventory.id,
        comment=inventory.comment,
    )
    db.add(history)
    await db.commit()
    logger.info(f"Car inventory with ID {db_inventory.id} created successfully", extra=extra)
    return db_inventory


async def get_car_inventory(db: AsyncSession, inventory_id: int, user_id: str = "N/A", request_id: str = "N/A"):
    """
    Retrieve a car inventory by its ID.

    Args:
        db (AsyncSession): The database session dependency.
        inventory_id (int): The ID of the inventory to fetch.
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        CarInventoryModel: The car inventory object if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Fetching inventory with ID: {inventory_id}", extra=extra)

    result = await db.execute(select(CarInventoryModel).where(CarInventoryModel.id == inventory_id))
    inventory = result.scalars().first()
    if not inventory:
        logger.warning(f"Inventory with ID {inventory_id} not found", extra=extra)
    else:
        logger.info(f"Inventory with ID {inventory_id} fetched successfully", extra=extra)
    return inventory


async def get_car_inventories(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 10,
    user_id: str = "N/A",
    request_id: str = "N/A",
):
    """
    Retrieve a paginated list of car inventories.

    Args:
        db (AsyncSession): The database session dependency.
        skip (int): Number of records to skip for pagination (default: 0).
        limit (int): Maximum number of records to return (default: 10).
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        List[CarInventoryModel]: A list of car inventory objects.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Fetching inventories with skip={skip}, limit={limit}", extra=extra)

    result = await db.execute(
        select(CarInventoryModel).options(selectinload(CarInventoryModel.car)).offset(skip).limit(limit)
    )
    inventories = result.scalars().all()
    logger.info(f"Returning {len(inventories)} inventories", extra=extra)
    return inventories


async def update_car_inventory(
    db: AsyncSession,
    inventory_id: int,
    inventory: CarInventoryUpdate,
    user_id: str = "N/A",
    request_id: str = "N/A",
):
    """
    Update a specific car inventory by its ID and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        inventory_id (int): The ID of the inventory to update.
        inventory (CarInventoryUpdate): The updated inventory data.
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        CarInventoryModel: The updated car inventory object if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Updating inventory with ID: {inventory_id}", extra=extra)

    db_inventory = await get_car_inventory(db, inventory_id, user_id, request_id)
    if db_inventory:
        update_data = inventory.dict(exclude_unset=True)
        action = "Updated: "
        for key, value in update_data.items():
            action += f"{key} {getattr(db_inventory, key)} -> {value}, "
            setattr(db_inventory, key, value)
        await update_inventory_financials(db, inventory_id)
        await db.commit()
        await db.refresh(db_inventory)

        # Create a history record
        history = HistoryModel(
            action=action,
            user_id=int(user_id),
            car_inventory_id=db_inventory.id,
            comment=inventory.comment,
        )
        db.add(history)
        await db.commit()
        logger.info(f"Inventory with ID {inventory_id} updated successfully", extra=extra)
    else:
        logger.error(f"Inventory with ID {inventory_id} not found for update", extra=extra)
    return db_inventory


async def delete_car_inventory(db: AsyncSession, inventory_id: int, user_id: str = "N/A", request_id: str = "N/A"):
    """
    Delete a specific car inventory by its ID and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        inventory_id (int): The ID of the inventory to delete.
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        CarInventoryModel: The deleted car inventory object if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Deleting inventory with ID: {inventory_id}", extra=extra)

    db_inventory = await get_car_inventory(db, inventory_id, user_id, request_id)
    if db_inventory:
        # Create a history record
        history = HistoryModel(action="Deleted", user_id=int(user_id), car_inventory_id=db_inventory.id)
        db.add(history)
        await db.commit()

        await db.delete(db_inventory)
        await db.commit()
        logger.info(f"Inventory with ID {inventory_id} deleted successfully", extra=extra)
    else:
        logger.error(f"Inventory with ID {inventory_id} not found for deletion", extra=extra)
    return db_inventory


# --- CarInventoryInvestments CRUD Operations ---


async def get_car_investment(db: AsyncSession, investment_id: int, user_id: str = "N/A", request_id: str = "N/A"):
    """
    Retrieve a specific investment by its ID.

    Args:
        db (AsyncSession): The database session dependency.
        investment_id (int): The ID of the investment to fetch.
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        CarInventoryInvestmentsModel: The investment object if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Fetching investment with ID: {investment_id}", extra=extra)

    result = await db.execute(
        select(CarInventoryInvestmentsModel)
        .options(selectinload(CarInventoryInvestmentsModel.car_inventory))
        .where(CarInventoryInvestmentsModel.id == investment_id)
    )
    investment = result.scalars().first()
    if not investment:
        logger.warning(f"Investment with ID {investment_id} not found", extra=extra)
    else:
        logger.info(f"Investment with ID {investment_id} fetched successfully", extra=extra)
    return investment


async def get_car_investments_by_inventory(
    db: AsyncSession, inventory_id: int, user_id: str = "N/A", request_id: str = "N/A"
):
    """
    Retrieve all investments for a specific car inventory.

    Args:
        db (AsyncSession): The database session dependency.
        inventory_id (int): The ID of the inventory to fetch investments for.
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        List[CarInventoryInvestmentsModel]: A list of investment objects for the inventory.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Fetching investments for inventory with ID: {inventory_id}", extra=extra)

    result = await db.execute(
        select(CarInventoryInvestmentsModel).where(CarInventoryInvestmentsModel.car_inventory_id == inventory_id)
    )
    investments = result.scalars().all()
    logger.info(
        f"Returning {len(investments)} investments for inventory with ID: {inventory_id}",
        extra=extra,
    )
    return investments


async def create_car_investment(
    db: AsyncSession,
    inventory_id: int,
    investment: CarInventoryInvestmentsCreate,
    user_id: str = "N/A",
    request_id: str = "N/A",
):
    """
    Create a new investment for a specific car inventory and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        inventory_id (int): The ID of the inventory to add the investment to.
        investment (CarInventoryInvestmentsCreate): The investment data to create.
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        CarInventoryInvestmentsModel: The created investment object if successful, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Creating investment for inventory with ID: {inventory_id}", extra=extra)

    db_inventory = await get_car_inventory(db, inventory_id, user_id, request_id)
    if db_inventory:
        data = investment.dict(exclude={"comment"})
        db_investment = CarInventoryInvestmentsModel(**data, car_inventory_id=inventory_id)
        db.add(db_investment)
        await update_inventory_financials(db, inventory_id)
        await db.commit()
        await db.refresh(db_inventory)
        await db.refresh(db_investment)

        # Create a history record
        history = HistoryModel(
            action=f"Added investment type {investment.investment_type}",
            user_id=int(user_id),
            car_inventory_id=inventory_id,
            comment=investment.comment,
        )
        db.add(history)
        await db.commit()
        logger.info(f"Investment created for inventory with ID: {inventory_id}", extra=extra)
    else:
        logger.error(
            f"Inventory with ID {inventory_id} not found for creating investment",
            extra=extra,
        )
    return db_investment


async def update_car_investment(
    db: AsyncSession,
    investment_id: int,
    investment: CarInventoryInvestmentsUpdate,
    user_id: str = "N/A",
    request_id: str = "N/A",
):
    """
    Update a specific investment by its ID and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        investment_id (int): The ID of the investment to update.
        investment (CarInventoryInvestmentsUpdate): The updated investment data.
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        CarInventoryInvestmentsModel: The updated investment object if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Updating investment with ID: {investment_id}", extra=extra)

    db_investment = await get_car_investment(db, investment_id, user_id, request_id)
    if db_investment:
        update_data = investment.dict(exclude_unset=True, exclude={"comment"})
        for key, value in update_data.items():
            setattr(db_investment, key, value)
        await db.commit()
        await db.refresh(db_investment)
        await update_inventory_financials(db, db_investment.car_inventory_id)
        await db.commit()

        # Create a history record
        history = HistoryModel(
            action=f"Updated investment type {db_investment.investment_type}",
            user_id=int(user_id),
            car_inventory_id=db_investment.car_inventory_id,
            comment=investment.comment,
        )
        db.add(history)
        await db.commit()
        logger.info(f"Investment with ID {investment_id} updated successfully", extra=extra)
    else:
        logger.error(f"Investment with ID {investment_id} not found for update", extra=extra)
    return db_investment


async def delete_car_investment(db: AsyncSession, investment_id: int, user_id: str = "N/A", request_id: str = "N/A"):
    """
    Delete a specific investment by its ID and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        investment_id (int): The ID of the investment to delete.
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        CarInventoryInvestmentsModel: The deleted investment object if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Deleting investment with ID: {investment_id}", extra=extra)

    db_investment = await get_car_investment(db, investment_id, user_id, request_id)
    if db_investment:
        inventory = db_investment.car_inventory
        car_inventory_id = db_investment.car_inventory_id

        # Create a history record
        history = HistoryModel(
            action=f"Deleted investment with ID {investment_id}",
            user_id=int(user_id),
            car_inventory_id=car_inventory_id,
        )
        db.add(history)
        await db.commit()

        await db.delete(db_investment)
        await db.commit()
        if inventory:
            await update_inventory_financials(db, car_inventory_id)
            await db.commit()
            await db.refresh(inventory)
        logger.info(f"Investment with ID {investment_id} deleted successfully", extra=extra)
    else:
        logger.error(f"Investment with ID {investment_id} not found for deletion", extra=extra)
    return db_investment


# --- PartInventory CRUD Operations ---


async def create_part_inventory(db: AsyncSession, part: PartInventoryCreate, user_id: str, request_id: str = "N/A"):
    """
    Create a new part inventory entry and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        part (PartInventoryCreate): The part inventory data to create.
        user_id (str): The ID of the user making the request.
        request_id (str): The request ID (for logging).

    Returns:
        PartInventoryModel: The created part inventory object.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Creating part inventory for user {user_id}", extra=extra)

    db_part = PartInventoryModel(**part.dict(exclude_unset=True))
    db.add(db_part)
    await db.commit()
    await db.refresh(db_part)

    # Create a history record
    history = HistoryModel(
        action="Added",
        user_id=int(user_id),
        part_inventory_id=db_part.id,
        comment=part.comment,
    )
    db.add(history)
    await db.commit()
    logger.info(f"Part inventory with ID {db_part.id} created successfully", extra=extra)
    return db_part


async def get_part_inventory(db: AsyncSession, part_id: int, user_id: str = "N/A", request_id: str = "N/A"):
    """
    Retrieve a specific part inventory by its ID.

    Args:
        db (AsyncSession): The database session dependency.
        part_id (int): The ID of the part inventory to fetch.
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        PartInventoryModel: The part inventory object if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Fetching part inventory with ID: {part_id}", extra=extra)

    result = await db.execute(
        select(PartInventoryModel)
        .options(
            selectinload(PartInventoryModel.history).selectinload(HistoryModel.user),
            selectinload(PartInventoryModel.invoices),
        )
        .where(PartInventoryModel.id == part_id)
    )
    db_part = result.scalars().first()
    if not db_part:
        logger.warning(f"Part inventory with ID {part_id} not found", extra=extra)
    else:
        logger.info(f"Part inventory with ID {part_id} fetched successfully", extra=extra)
    return db_part


async def get_part_inventories(db: AsyncSession, user_id: str = "N/A", request_id: str = "N/A"):
    """
    Retrieve all part inventories.

    Args:
        db (AsyncSession): The database session dependency.
        user_id (str): The ID of the user making the request (for logging).
        request_id (str): The request ID (for logging).

    Returns:
        List[PartInventoryModel]: A list of part inventory objects.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info("Fetching all part inventories", extra=extra)

    result = await db.execute(
        select(PartInventoryModel).options(
            selectinload(PartInventoryModel.invoices),
            selectinload(PartInventoryModel.history).selectinload(HistoryModel.user),
        )
    )
    parts = result.scalars().all()
    logger.info(f"Returning {len(parts)} part inventories", extra=extra)
    return parts


async def update_part_inventory(
    db: AsyncSession,
    part_id: int,
    part: PartInventoryUpdate,
    user_id: str,
    request_id: str = "N/A",
):
    """
    Update a specific part inventory by its ID and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        part_id (int): The ID of the part inventory to update.
        part (PartInventoryUpdate): The updated part inventory data.
        user_id (str): The ID of the user making the request.
        request_id (str): The request ID (for logging).

    Returns:
        PartInventoryModel: The updated part inventory object if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Updating part inventory with ID: {part_id}", extra=extra)

    db_part = await get_part_inventory(db, part_id, user_id, request_id)
    if not db_part:
        logger.error(f"Part inventory with ID {part_id} not found for update", extra=extra)
        return None

    update_data = part.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_part, key, value)

    await db.commit()
    await db.refresh(db_part)

    # Create a history record
    history = HistoryModel(
        action="Updated",
        user_id=int(user_id),
        part_inventory_id=db_part.id,
        comment=part.comment,
    )
    db.add(history)
    await db.commit()
    logger.info(f"Part inventory with ID {part_id} updated successfully", extra=extra)
    return db_part


async def delete_part_inventory(db: AsyncSession, part_id: int, user_id: str, request_id: str = "N/A"):
    """
    Delete a specific part inventory by its ID and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        part_id (int): The ID of the part inventory to delete.
        user_id (str): The ID of the user making the request.
        request_id (str): The request ID (for logging).

    Returns:
        dict: A message indicating successful deletion if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Deleting part inventory with ID: {part_id}", extra=extra)

    db_part = await get_part_inventory(db, part_id, user_id, request_id)
    if not db_part:
        logger.error(f"Part inventory with ID {part_id} not found for deletion", extra=extra)
        return None

    # Create a history record
    history = HistoryModel(action="Deleted", user_id=int(user_id), part_inventory_id=db_part.id)
    db.add(history)
    await db.commit()

    await db.delete(db_part)
    await db.commit()
    logger.info(f"Part inventory with ID {part_id} deleted successfully", extra=extra)
    return {"message": "Part deleted successfully"}


async def update_part_status(
    db: AsyncSession,
    part_id: int,
    status_update: PartInventoryStatusUpdate,
    user_id: str,
    request_id: str = "N/A",
):
    """
    Update the status of a specific part inventory and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        part_id (int): The ID of the part inventory to update.
        status_update (PartInventoryStatusUpdate): The new status and optional comment.
        user_id (str): The ID of the user making the request.
        request_id (str): The request ID (for logging).

    Returns:
        PartInventoryModel: The updated part inventory object if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Updating status for part inventory with ID: {part_id}", extra=extra)

    db_part = await get_part_inventory(db, part_id, user_id, request_id)
    if not db_part:
        logger.error(f"Part inventory with ID {part_id} not found for status update", extra=extra)
        return None

    previous_status = db_part.part_status
    db_part.part_status = status_update.part_status
    await db.commit()
    await db.refresh(db_part)

    history = HistoryModel(
        action=f"Status changed from {previous_status.value} to {status_update.part_status.value}",
        user_id=int(user_id),
        part_inventory_id=db_part.id,
        comment=status_update.comment,
    )
    db.add(history)
    await db.commit()
    logger.info(f"Status for part inventory with ID {part_id} updated successfully", extra=extra)
    return db_part


async def upload_invoice(
    db: AsyncSession,
    part_id: int,
    file_data: bytes,
    file_name: str,
    user_id: str,
    request_id: str = "N/A",
):
    """
    Upload an invoice file to S3 and link it to a part inventory, logging the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        part_id (int): The ID of the part inventory to link the invoice to.
        file_data (bytes): The binary data of the invoice file.
        file_name (str): The name of the file to store in S3.
        user_id (str): The ID of the user making the request.
        request_id (str): The request ID (for logging).

    Returns:
        InvoiceModel: The created invoice object if successful, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Uploading invoice for part inventory with ID: {part_id}", extra=extra)

    db_part = await get_part_inventory(db, part_id, user_id, request_id)
    if not db_part:
        logger.error(
            f"Part inventory with ID {part_id} not found for invoice upload",
            extra=extra,
        )
        return None

    s3_client = get_s3_storage_client()
    s3_key = f"invoices/{part_id}/{file_name}_{user_id}_{int(datetime.now().timestamp())}_invoice.{file_name.split('.')[-1]}"
    s3_client.upload_file(file_data=file_data, file_name=s3_key)
    file_url = f"{settings.S3_STORAGE_ENDPOINT}/{settings.S3_BUCKET_NAME}/{s3_key}"

    db_invoice = InvoiceModel(part_inventory_id=part_id, file_url=file_url)
    db.add(db_invoice)
    await db.commit()
    await db.refresh(db_invoice)

    history = HistoryModel(
        action=f"Invoice uploaded (ID: {db_invoice.id})",
        user_id=int(user_id),
        part_inventory_id=db_part.id,
    )
    db.add(history)
    await db.commit()
    logger.info(
        f"Invoice (ID: {db_invoice.id}) uploaded for part inventory with ID {part_id}",
        extra=extra,
    )
    return db_invoice


async def delete_invoice(db: AsyncSession, invoice_id: int, user_id: str, request_id: str = "N/A"):
    """
    Delete an invoice linked to a part inventory and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        invoice_id (int): The ID of the invoice to delete.
        user_id (str): The ID of the user making the request.
        request_id (str): The request ID (for logging).

    Returns:
        dict: A message indicating successful deletion if found, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Deleting invoice with ID: {invoice_id}", extra=extra)

    db_invoice = await db.get(InvoiceModel, invoice_id)
    part_inventory_id = db_invoice.part_inventory_id if db_invoice else None
    if db_invoice:
        s3_client = get_s3_storage_client()
        s3_key = db_invoice.file_url.replace("https://my-inventory-bucket.s3.amazonaws.com/", "")
        s3_client.delete_file(file_name=s3_key)

        # Create a history record
        history = HistoryModel(
            action=f"Invoice deleted (ID: {invoice_id})",
            user_id=int(user_id),
            part_inventory_id=part_inventory_id,
        )
        db.add(history)
        await db.commit()

        await db.delete(db_invoice)
        await db.commit()
        logger.info(
            f"Invoice deleted for part inventory with ID {part_inventory_id}",
            extra=extra,
        )
        return {"message": "Invoice deleted successfully"}
    logger.warning(f"Invoice with ID {invoice_id} not found", extra=extra)
    return None


async def update_invoice(
    db: AsyncSession,
    invoice_id: int,
    file_data: bytes,
    file_name: str,
    user_id: str,
    request_id: str = "N/A",
):
    """
    Update an existing invoice by uploading a new file to S3 and log the action in history.

    Args:
        db (AsyncSession): The database session dependency.
        invoice_id (int): The ID of the invoice to update.
        file_data (bytes): The binary data of the new invoice file.
        file_name (str): The name of the new file to store in S3.
        user_id (str): The ID of the user making the request.
        request_id (str): The request ID (for logging).

    Returns:
        InvoiceModel: The updated invoice object if successful, otherwise None.
    """
    extra = {"request_id": request_id, "user_id": user_id}
    logger.info(f"Updating invoice with ID: {invoice_id}", extra=extra)

    db_invoice = await db.get(InvoiceModel, invoice_id)
    part_inventory_id = db_invoice.part_inventory_id if db_invoice else None
    if db_invoice:
        s3_client = get_s3_storage_client()
        s3_key = f"invoices/{part_inventory_id}/{file_name}_{user_id}_{int(datetime.now().timestamp())}"
        s3_client.upload_file(file_data=file_data, file_name="my-inventory-bucket")
        new_file_url = f"https://my-inventory-bucket.s3.amazonaws.com/{s3_key}"

        # Delete the old file if it exists
        if db_invoice.file_url:
            old_s3_key = db_invoice.file_url.replace("https://my-inventory-bucket.s3.amazonaws.com/", "")
            s3_client.delete_file(file_name=old_s3_key)

        db_invoice.file_url = new_file_url
        await db.commit()
        await db.refresh(db_invoice)

        # Create a history record
        history = HistoryModel(
            action=f"Invoice updated (ID: {invoice_id})",
            user_id=int(user_id),
            part_inventory_id=part_inventory_id,
        )
        db.add(history)
        await db.commit()
        logger.info(
            f"Invoice updated for part inventory with ID {part_inventory_id}",
            extra=extra,
        )
        return db_invoice
    logger.warning(f"Invoice with ID {invoice_id} not found", extra=extra)
    return None
