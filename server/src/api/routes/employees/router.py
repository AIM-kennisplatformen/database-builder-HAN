from fastapi import APIRouter

from api.routes.employees.sync import router as sync_router

router = APIRouter(tags=["Employees"], prefix="/api/v1/employees")
router.include_router(sync_router)
