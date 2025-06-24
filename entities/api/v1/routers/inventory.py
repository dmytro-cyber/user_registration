from fastapi import APIRouter, Depends, HTTPException, status, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc
from sqlalchemy.orm import selectinload
from typing import List, Optional
from crud.inventory import (
    get_car_inventories,
    get_car_inventory,
    update_car_inventory,
    delete_car_inventory,
    get_car_investments_by_inventory,
    get_car_investment,
    create_car_investment,
    update_car_investment,
    delete_car_investment,
    create_part_inventory,
    get_part_inventory,
    get_part_inventories,
    update_part_inventory,
    delete_part_inventory,
    update_invoice,
    upload_invoice,
    delete_invoice,
    update_part_status,
    create_car_inventory,
)
from models.user import UserModel
from schemas.inventory import (
    CarInventoryCreate,
    CarInventoryUpdate,
    CarInventoryUpdateStatus,
    CarInventoryResponse,
    CarInventoryInvestmentsCreate,
    CarInventoryInvestmentsUpdate,
    CarInventoryInvestmentsResponse,
    PartInventoryCreate,
    PartInventoryUpdate,
    PartInventoryResponse,
    HistoryResponse,
    PartInventoryStatusUpdate,
    InvoiceResponse,
    CarInventoryDetailResponse,
)
from schemas.vehicle import (
    BiddingHubHistoryListResponseSchema,
    BiddingHubHistorySchema,
)
from schemas.user import UserResponseSchema
from models.vehicle import PartInventoryModel, HistoryModel
from models.user import UserModel
from db.session import get_db
from core.dependencies import get_current_user

router = APIRouter(prefix="/inventory")


@router.post("/vehicles/", response_model=CarInventoryResponse, status_code=status.HTTP_201_CREATED)
async def create_inventory(
    inventory: CarInventoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    db_inventory = await create_car_inventory(db, inventory, user_id=str(current_user.id))
    return CarInventoryResponse(**db_inventory.__dict__, comment=inventory.comment)


@router.get("/vehicles/", response_model=List[CarInventoryResponse])
async def read_inventories(
    skip: int = 0, limit: int = 10, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)
):
    inventories = await get_car_inventories(db, skip, limit, user_id=str(current_user.id))
    responses = []
    for inventory in inventories:
        result = await db.execute(
            select(HistoryModel)
            .options(selectinload(HistoryModel.user))
            .where(HistoryModel.car_inventory_id == inventory.id)
            .order_by(desc(HistoryModel.created_at))
            .limit(1)
        )
        latest_history = result.scalars().first()
        fullname = None
        if latest_history and latest_history.user:
            fullname = f"{latest_history.user.first_name} {latest_history.user.last_name}"
        responses.append(CarInventoryResponse(**inventory.__dict__, fullname=fullname))
    return responses


@router.get("/vehicles/{inventory_id}", response_model=CarInventoryDetailResponse)
async def read_inventory(
    inventory_id: int, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)
):
    inventory = await get_car_inventory(db, inventory_id, user_id=str(current_user.id))
    if inventory is None:
        raise HTTPException(status_code=404, detail="Inventory not found")

    result = await db.execute(
        select(HistoryModel)
        .where(HistoryModel.car_inventory_id == inventory.id)
        .order_by(desc(HistoryModel.created_at))
        .limit(1)
    )
    latest_history = result.scalars().first()
    comment = latest_history.comment if latest_history else None
    return CarInventoryDetailResponse(**inventory.__dict__, comment=comment)


@router.put("/vehicles/{inventory_id}", response_model=CarInventoryResponse)
async def update_inventory(
    inventory_id: int,
    inventory: CarInventoryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    db_inventory = await update_car_inventory(db, inventory_id, inventory, user_id=str(current_user.id))
    if db_inventory is None:
        raise HTTPException(status_code=404, detail="Inventory not found")

    result = await db.execute(
        select(HistoryModel)
        .where(HistoryModel.car_inventory_id == db_inventory.id)
        .order_by(desc(HistoryModel.created_at))
        .limit(1)
    )
    latest_history = result.scalars().first()
    comment = latest_history.comment if latest_history else None
    return CarInventoryResponse(**db_inventory.__dict__, comment=comment)


@router.patch("/vehicles/{inventory_id}/status", response_model=CarInventoryResponse)
async def update_inventory_status(
    inventory_id: int,
    inventory: CarInventoryUpdateStatus,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    db_inventory = await get_car_inventory(db, inventory_id, user_id=str(current_user.id))
    if db_inventory is None:
        raise HTTPException(status_code=404, detail="Inventory not found")

    previous_status = db_inventory.car_status
    db_inventory = await update_car_inventory(db, inventory_id, inventory, user_id=str(current_user.id))
    if db_inventory is None:
        raise HTTPException(status_code=404, detail="Inventory not found")

    result = await db.execute(
        select(HistoryModel)
        .where(HistoryModel.car_inventory_id == db_inventory.id)
        .order_by(desc(HistoryModel.created_at))
        .limit(1)
    )
    latest_history = result.scalars().first()
    comment = latest_history.comment if latest_history else None
    return CarInventoryResponse(**db_inventory.__dict__, comment=comment)


@router.delete("/vehicles/{inventory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inventory(
    inventory_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    comment: Optional[str] = None,
):
    db_inventory = await delete_car_inventory(db, inventory_id, user_id=str(current_user.id))
    if db_inventory is None:
        raise HTTPException(status_code=404, detail="Inventory not found")


@router.get("/vehicles/{inventory_id}/investments/", response_model=List[CarInventoryInvestmentsResponse])
async def read_investments(inventory_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    investments = await get_car_investments_by_inventory(db, inventory_id, user_id=str(user.id))
    responses = []
    for investment in investments:
        result = await db.execute(
            select(HistoryModel)
            .where(HistoryModel.car_inventory_id == investment.car_inventory_id)
            .where(HistoryModel.action.ilike(f"%investment%ID {investment.id}%"))
            .order_by(desc(HistoryModel.created_at))
            .limit(1)
        )
        latest_history = result.scalars().first()
        comment = latest_history.comment if latest_history else None
        responses.append(CarInventoryInvestmentsResponse(**investment.__dict__, comment=comment))
    return responses


@router.get("/vehicles/{inventory_id}/investments/{investment_id}", response_model=CarInventoryInvestmentsResponse)
async def read_investment(
    inventory_id: int, investment_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)
):
    investment = await get_car_investment(db, investment_id, user_id=str(user.id))
    if investment is None or investment.car_inventory_id != inventory_id:
        raise HTTPException(status_code=404, detail="Investment not found")

    result = await db.execute(
        select(HistoryModel)
        .where(HistoryModel.car_inventory_id == investment.car_inventory_id)
        .where(HistoryModel.action.ilike(f"%investment%ID {investment.id}%"))
        .order_by(desc(HistoryModel.created_at))
        .limit(1)
    )
    latest_history = result.scalars().first()
    comment = latest_history.comment if latest_history else None
    return CarInventoryInvestmentsResponse(**investment.__dict__, comment=comment)


@router.post(
    "/vehicles/{inventory_id}/investments/",
    response_model=CarInventoryInvestmentsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_investment(
    inventory_id: int,
    investment: CarInventoryInvestmentsCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    db_investment = await create_car_investment(db, inventory_id, investment, user_id=str(current_user.id))
    if db_investment is None:
        raise HTTPException(status_code=404, detail="Inventory not found")
    return CarInventoryInvestmentsResponse(**db_investment.__dict__, comment=investment.comment)


@router.put("/vehicles/{inventory_id}/investments/{investment_id}", response_model=CarInventoryInvestmentsResponse)
async def update_investment(
    inventory_id: int,
    investment_id: int,
    investment: CarInventoryInvestmentsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    db_investment = await update_car_investment(db, investment_id, investment, user_id=str(current_user.id))
    if db_investment is None or db_investment.car_inventory_id != inventory_id:
        raise HTTPException(status_code=404, detail="Investment not found")
    return CarInventoryInvestmentsResponse(**db_investment.__dict__)


@router.delete("/vehicles/{inventory_id}/investments/{investment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_investment(
    inventory_id: int,
    investment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    comment: Optional[str] = None,
):
    db_investment = await delete_car_investment(db, investment_id, user_id=str(current_user.id))
    if db_investment is None or db_investment.car_inventory_id != inventory_id:
        raise HTTPException(status_code=404, detail="Investment not found")


@router.post("/parts/", response_model=PartInventoryResponse)
async def post_part_inventory(
    part: PartInventoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    db_part = await create_part_inventory(db, part, user_id=str(current_user.id))

    # Fetch the latest history record for fullname
    result = await db.execute(
        select(HistoryModel)
        .options(selectinload(HistoryModel.user))
        .where(HistoryModel.part_inventory_id == db_part.id)
        .order_by(desc(HistoryModel.created_at))
        .limit(1)
    )
    latest_history = result.scalars().first()
    fullname = None
    if latest_history and latest_history.user:
        fullname = f"{latest_history.user.first_name} {latest_history.user.last_name}"

    return PartInventoryResponse(**db_part.__dict__, fullname=fullname)


@router.get("/parts/", response_model=List[PartInventoryResponse])
async def get_part_inventory_endpoint(db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    parts = await get_part_inventories(db, user_id=str(current_user.id))
    for part in parts:
        result = await db.execute(
            select(HistoryModel)
            .options(selectinload(HistoryModel.user))
            .where(HistoryModel.part_inventory_id == part.id)
            .order_by(desc(HistoryModel.created_at))
            .limit(1)
        )
        latest_history = result.scalars().first()
        part.fullname = None
        if latest_history and latest_history.user:
            part.fullname = f"{latest_history.user.first_name} {latest_history.user.last_name}"
    return parts


@router.get("/parts/{part_id}", response_model=PartInventoryResponse)
async def get_part_inventory_by_id(
    part_id: int, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)
):
    db_part = await get_part_inventory(db, part_id, user_id=str(current_user.id))
    if not db_part:
        raise HTTPException(status_code=404, detail="Part not found")

    result = await db.execute(
        select(HistoryModel)
        .options(selectinload(HistoryModel.user))
        .where(HistoryModel.part_inventory_id == db_part.id)
        .order_by(desc(HistoryModel.created_at))
        .limit(1)
    )
    latest_history = result.scalars().first()
    fullname = None
    if latest_history and latest_history.user:
        fullname = f"{latest_history.user.first_name} {latest_history.user.last_name}"

    return PartInventoryResponse(**db_part.__dict__, fullname=fullname)


@router.put("/parts/{part_id}", response_model=PartInventoryResponse)
async def put_update_part_inventory(
    part_id: int,
    part: PartInventoryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    db_part = await update_part_inventory(db, part_id, part, user_id=str(current_user.id))
    if not db_part:
        raise HTTPException(status_code=404, detail="Part not found")

    result = await db.execute(
        select(HistoryModel)
        .options(selectinload(HistoryModel.user))
        .where(HistoryModel.part_inventory_id == db_part.id)
        .order_by(desc(HistoryModel.created_at))
        .limit(1)
    )
    latest_history = result.scalars().first()
    fullname = None
    if latest_history and latest_history.user:
        fullname = f"{latest_history.user.first_name} {latest_history.user.last_name}"

    return PartInventoryResponse(**db_part.__dict__, fullname=fullname)


@router.delete("/parts/{part_id}", response_model=dict)
async def delete_part_inventory_endpoint(
    part_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    comment: Optional[str] = None,
):
    result = await delete_part_inventory(db, part_id, user_id=str(current_user.id))
    if not result:
        raise HTTPException(status_code=404, detail="Part not found")
    return result


@router.patch("/part-inventory/{part_id}/status", response_model=PartInventoryResponse)
async def update_part_status_endpoint(
    part_id: int,
    status_update: PartInventoryStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    db_part = await update_part_status(db, part_id, status_update, user_id=str(current_user.id))
    if not db_part:
        raise HTTPException(status_code=404, detail="Part not found")

    result = await db.execute(
        select(HistoryModel)
        .options(selectinload(HistoryModel.user))
        .where(HistoryModel.part_inventory_id == db_part.id)
        .order_by(desc(HistoryModel.created_at))
        .limit(1)
    )
    latest_history = result.scalars().first()
    fullname = None
    if latest_history and latest_history.user:
        fullname = f"{latest_history.user.first_name} {latest_history.user.last_name}"

    return PartInventoryResponse(**db_part.__dict__, fullname=fullname)


@router.post("/parts/{part_id}/invoice", response_model=InvoiceResponse)
async def upload_invoice_endpoint(
    part_id: int,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
) -> InvoiceResponse:
    file_data = await file.read()
    db_invoice = await upload_invoice(db, part_id, file_data, file.filename, user_id=str(current_user.id))
    if not db_invoice:
        raise HTTPException(status_code=404, detail="Part not found or invoice upload failed")
    return db_invoice


@router.delete("/parts/invoice/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice_endpoint(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    comment: Optional[str] = None,
):
    result = await delete_invoice(db, invoice_id, user_id=str(current_user.id))
    if not result:
        raise HTTPException(status_code=404, detail="Part not found or no invoice to delete")


@router.put("/parts/invoice/{invoice_id}", response_model=dict)
async def update_invoice_endpoint(
    invoice_id: int,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    file_data = await file.read()
    db_invoice = await update_invoice(db, invoice_id, file_data, file.filename, user_id=str(current_user.id))
    if not db_invoice:
        raise HTTPException(status_code=404, detail="Part not found or no invoice to update")
    return {"message": "Invoice updated successfully", "file_url": db_invoice.file_url}


@router.get("/parts/{part_id}/invoices/", response_model=List[InvoiceResponse])
async def get_invoices_by_part_id(
    part_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    db_part = await get_part_inventory(db, part_id, user_id=str(current_user.id))
    if not db_part:
        raise HTTPException(status_code=404, detail="Part not found")

    invoices = db_part.invoices
    return invoices


@router.get(
    "vehicles/history/{car_inventory_id}",
    response_model=BiddingHubHistoryListResponseSchema,
    summary="Get bidding hub history for a vehicle",
    description="Retrieve the bidding hub history for a vehicle by its ID, including full user details, ordered by creation date (descending).",
)
async def get_car_inventory_history(
    car_inventory_id: int,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BiddingHubHistoryListResponseSchema:
    """
    Get bidding hub history for a vehicle by ID, including full user details.

    Args:
        car_inventory_id (int): The ID of the vehicle to fetch history for.
        current_user (Settings): The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Returns:
        BiddingHubHistoryListResponseSchema: The history of bidding actions for the vehicle.

    Raises:
        HTTPException: 404 if no bidding history is found for the vehicle.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}

    try:
        stmt = (
            select(HistoryModel)
            .where(HistoryModel.car_inventory_id == car_inventory_id)
            .options(selectinload(HistoryModel.user).selectinload(UserModel.role))
            .order_by(HistoryModel.created_at.desc())
        )
        result = await db.execute(stmt)
        history_list = result.scalars().all()
        if not history_list:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No bidding history found for this vehicle"
            )
        return BiddingHubHistoryListResponseSchema(
            history=[
                BiddingHubHistorySchema(
                    id=item.id,
                    action=item.action,
                    user=(
                        UserResponseSchema(
                            email=item.user.email,
                            first_name=item.user.first_name,
                            last_name=item.user.last_name,
                            phone_number=item.user.phone_number,
                            date_of_birth=item.user.date_of_birth,
                            role=item.user.role.name if item.user.role else None,
                        )
                        if item.user
                        else None
                    ),
                    comment=item.comment,
                    created_at=item.created_at,
                )
                for item in history_list
            ]
        )
    except HTTPException as e:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching bidding history",
        )


@router.get(
    "vehicles/history/{part_inventory_id}",
    response_model=BiddingHubHistoryListResponseSchema,
    summary="Get bidding hub history for a vehicle",
    description="Retrieve the bidding hub history for a vehicle by its ID, including full user details, ordered by creation date (descending).",
)
async def get_part_inventory_history(
    part_inventory_id: int,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BiddingHubHistoryListResponseSchema:
    """
    Get bidding hub history for a vehicle by ID, including full user details.

    Args:
        part_inventory_id (int): The ID of the vehicle to fetch history for.
        current_user (Settings): The currently authenticated user.
        db (AsyncSession): The database session dependency.

    Returns:
        BiddingHubHistoryListResponseSchema: The history of bidding actions for the vehicle.

    Raises:
        HTTPException: 404 if no bidding history is found for the vehicle.
    """
    request_id = "N/A"  # No request object available here
    extra = {"request_id": request_id, "user_id": getattr(current_user, "id", "N/A")}

    try:
        stmt = (
            select(HistoryModel)
            .where(HistoryModel.part_inventory_id == part_inventory_id)
            .options(selectinload(HistoryModel.user).selectinload(UserModel.role))
            .order_by(HistoryModel.created_at.desc())
        )
        result = await db.execute(stmt)
        history_list = result.scalars().all()
        if not history_list:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No bidding history found for this vehicle"
            )
        return BiddingHubHistoryListResponseSchema(
            history=[
                BiddingHubHistorySchema(
                    id=item.id,
                    action=item.action,
                    user=(
                        UserResponseSchema(
                            email=item.user.email,
                            first_name=item.user.first_name,
                            last_name=item.user.last_name,
                            phone_number=item.user.phone_number,
                            date_of_birth=item.user.date_of_birth,
                            role=item.user.role.name if item.user.role else None,
                        )
                        if item.user
                        else None
                    ),
                    comment=item.comment,
                    created_at=item.created_at,
                )
                for item in history_list
            ]
        )
    except HTTPException as e:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching bidding history",
        )
