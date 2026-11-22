"""Upload IB and RHA workbooks as UBWFRIS snapshots."""

from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.errors import PublicError, internal_error, log_api_failure
from config import config
from dependencies import (
    get_current_graph_user_id,
    get_ubwfris_ib_deleter,
    get_ubwfris_ib_parser,
    get_ubwfris_ib_writer,
    get_ubwfris_rha_deleter,
    get_ubwfris_rha_parser,
    get_ubwfris_rha_writer,
    require_ubwfris_delete_enabled,
)
from domain.exceptions.error import DomainError
from domain.exceptions.ubwfris_ingest_errors import (
    DuplicateUpload,
    InvalidWorkbook,
    ParserFailure,
    StoreUnavailable,
)
from domain.models.ubwfris import (
    IbRow,
    RhaRow,
    UbwfrisIngestProgress,
    UbwfrisIngestRequest,
    UbwfrisIngestResult,
    UbwfrisRow,
    UbwfrisWorkbook,
)
from domain.ports.ubwfris.ubwfris_parser import UbwfrisParser
from domain.ports.ubwfris.ubwfris_store import UbwfrisSnapshotDeleter, UbwfrisWriter
from domain.use_cases.ubwfris_ingest import ingest_ubwfris_workbook

router = APIRouter()


@router.delete(
    "/ib",
    summary="Delete all IB snapshots",
    include_in_schema=config.delete_endpoints.ubwfris,
)
async def delete_ib_snapshots(
    _delete_enabled: Annotated[None, Depends(require_ubwfris_delete_enabled)],
    _principal_id: Annotated[str, Depends(get_current_graph_user_id)],
    writer: Annotated[UbwfrisSnapshotDeleter, Depends(get_ubwfris_ib_deleter)],
) -> dict[str, bool]:
    if not await writer.delete_all_snapshots("ib"):
        raise HTTPException(
            status_code=409, detail="IB snapshots cannot be reset during import"
        )
    return {"deleted": True}


@router.delete(
    "/rha",
    summary="Delete all RHA snapshots",
    include_in_schema=config.delete_endpoints.ubwfris,
)
async def delete_rha_snapshots(
    _delete_enabled: Annotated[None, Depends(require_ubwfris_delete_enabled)],
    _principal_id: Annotated[str, Depends(get_current_graph_user_id)],
    writer: Annotated[UbwfrisSnapshotDeleter, Depends(get_ubwfris_rha_deleter)],
) -> dict[str, bool]:
    if not await writer.delete_all_snapshots("rha"):
        raise HTTPException(
            status_code=409, detail="RHA snapshots cannot be reset during import"
        )
    return {"deleted": True}


class UbwfrisIngestResponse(BaseModel):
    operation_id: UUID
    snapshot_id: str
    row_type: Literal["ib", "rha"]
    total_rows: int
    processed_rows: int
    created_rows: int
    invalid_rows: int
    invalid_row_numbers: list[int]


class UbwfrisErrorMapper:
    @staticmethod
    def describe(exc: Exception) -> PublicError:
        if isinstance(exc, InvalidWorkbook):
            return PublicError("ubwfris.invalid_workbook", 422, "Workbook is invalid")
        if isinstance(exc, DuplicateUpload):
            return PublicError(
                "ubwfris.duplicate_upload", 409, "This workbook was already imported"
            )
        if isinstance(exc, ParserFailure):
            return PublicError("ubwfris.parser_failure", 500, "Workbook parsing failed")
        if isinstance(exc, StoreUnavailable):
            return PublicError(
                "ubwfris.store_unavailable", 503, "UBWFRIS store is unavailable"
            )
        return internal_error()


async def _upload_ubwfris_workbook[T: UbwfrisRow](
    file: UploadFile,
    parser: UbwfrisParser[T],
    writer: UbwfrisWriter[T],
    principal_id: str,
    row_type: Literal["ib", "rha"],
) -> UbwfrisIngestResponse | JSONResponse:
    workbook = UbwfrisWorkbook[T](await file.read())
    snapshot_id = sha256(workbook.file).hexdigest()
    operation_id = uuid4()
    last_progress: UbwfrisIngestProgress | None = None
    updates = ingest_ubwfris_workbook(
        UbwfrisIngestRequest(workbook),
        parser,
        writer,
        snapshot_id,
        operation_id,
        principal_id,
    )

    try:
        result: UbwfrisIngestResult | None = None
        async for update in updates:
            if isinstance(update, UbwfrisIngestProgress):
                last_progress = update
            else:
                result = update
        if result is None:
            raise RuntimeError("UBWFRIS ingest ended without a result")
    except Exception as exc:  # noqa: BLE001 - map ingest failures to safe HTTP errors
        public_error = UbwfrisErrorMapper.describe(exc)
        log_api_failure(
            "ubwfris",
            exc,
            public_error,
            identifiers={
                "operation": "ubwfris.ingest",
                "operation_id": str(operation_id),
                "snapshot_id": snapshot_id,
            },
        )
        failure_summary: dict[str, object] = (
            dict(exc.failure_summary)
            if isinstance(exc, DomainError) and exc.failure_summary is not None
            else {
                "processed_rows": last_progress.processed_rows if last_progress else 0,
                "staged_rows": last_progress.created_rows if last_progress else 0,
                "published_rows": 0,
                "invalid_rows": 0,
                "invalid_row_numbers": [],
            }
        )
        failure_summary["published_rows"] = 0
        return JSONResponse(
            status_code=public_error.http_status,
            content={
                "operation_id": str(operation_id),
                "snapshot_id": snapshot_id,
                "error": public_error.as_dict(),
                "summary": failure_summary,
            },
        )
    finally:
        await updates.aclose()

    return UbwfrisIngestResponse(
        operation_id=operation_id,
        snapshot_id=snapshot_id,
        row_type=row_type,
        total_rows=result.total_rows,
        processed_rows=result.processed_rows,
        created_rows=result.created_rows,
        invalid_rows=result.invalid_rows,
        invalid_row_numbers=list(result.invalid_row_numbers),
    )


@router.post(
    "/ib",
    response_model=UbwfrisIngestResponse,
    summary="Import an IB workbook snapshot",
    description="Import an IB workbook and return its completed snapshot and operation IDs.",
)
async def upload_ib_workbook(
    file: Annotated[UploadFile, File(description="IB Excel workbook")],
    principal_id: Annotated[str, Depends(get_current_graph_user_id)],
    parser: Annotated[UbwfrisParser[IbRow], Depends(get_ubwfris_ib_parser)],
    writer: Annotated[UbwfrisWriter[IbRow], Depends(get_ubwfris_ib_writer)],
) -> UbwfrisIngestResponse | JSONResponse:
    return await _upload_ubwfris_workbook(file, parser, writer, principal_id, "ib")


@router.post(
    "/rha",
    response_model=UbwfrisIngestResponse,
    summary="Import an RHA workbook snapshot",
    description="Import an RHA workbook and return its completed snapshot and operation IDs.",
)
async def upload_rha_workbook(
    file: Annotated[UploadFile, File(description="RHA Excel workbook")],
    principal_id: Annotated[str, Depends(get_current_graph_user_id)],
    parser: Annotated[UbwfrisParser[RhaRow], Depends(get_ubwfris_rha_parser)],
    writer: Annotated[UbwfrisWriter[RhaRow], Depends(get_ubwfris_rha_writer)],
) -> UbwfrisIngestResponse | JSONResponse:
    return await _upload_ubwfris_workbook(file, parser, writer, principal_id, "rha")
