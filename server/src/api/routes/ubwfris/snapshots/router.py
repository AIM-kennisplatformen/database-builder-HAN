from fastapi import APIRouter

from api.routes.ubwfris.snapshots.ingest import router as ingest_router

router = APIRouter(tags=["UBWFRIS snapshots"], prefix="/api/v1/ubwfris/snapshots")
router.include_router(ingest_router)
