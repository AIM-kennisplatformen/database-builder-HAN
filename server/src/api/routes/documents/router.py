from fastapi import APIRouter

from api.routes.documents.drives import router as drives_router
from api.routes.documents.metadata import router as metadata_router

router = APIRouter(tags=["Documents"], prefix="/api/v1/documents")
router.include_router(drives_router)
router.include_router(metadata_router)
