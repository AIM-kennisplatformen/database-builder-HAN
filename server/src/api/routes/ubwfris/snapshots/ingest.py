"""Upload IB and RHA workbooks as UBWFRIS snapshots."""

from collections.abc import AsyncGenerator
from enum import StrEnum
from hashlib import sha256
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.sse import EventSourceResponse, ServerSentEvent
from frozendict import frozendict
from pydantic import BaseModel

from api.progress import ProgressEventLimiter
from api.stream import StreamEvent, StreamEventKind, StreamLifecycle
from config import config
from dependencies import (
    get_ubwfris_ib_parser,
    get_ubwfris_ib_writer,
    get_ubwfris_rha_parser,
    get_ubwfris_rha_writer,
)
from domain.exceptions.ubwfris_ingest_errors import (
    DuplicateUpload,
    InvalidWorkbook,
    ParserFailure,
    StoreUnavailable,
)
from domain.models.ubwfris import (
    IbRow,
    RhaRow,
    UbwfrisIngestEvent,
    UbwfrisIngestProgress,
    UbwfrisIngestRequest,
    UbwfrisRow,
    UbwfrisWorkbook,
)
from domain.ports.ubwfris.ubwfris_parser import UbwfrisParser
from domain.ports.ubwfris.ubwfris_store import UbwfrisWriter
from domain.use_cases.ubwfris_ingest import ingest_ubwfris_workbook

router = APIRouter()

UBWFRIS_ERROR_STATUS: frozendict[type[Exception], int] = frozendict(
    {
        InvalidWorkbook: 422,
        DuplicateUpload: 409,
        ParserFailure: 500,
        StoreUnavailable: 503,
    }
)


class UbwfrisIngestProgressStatus(StrEnum):
    PROGRESS = "progress"
    COMPLETE = "complete"
    ERROR = "error"


class UbwfrisIngestProgressEvent(BaseModel):
    status: UbwfrisIngestProgressStatus
    processed_rows: int
    total_rows: int
    created_rows: int
    invalid_rows: int
    snapshot_id: str | None = None
    invalid_row_numbers: list[int] | None = None
    error_status: int | None = None
    detail: str | None = None


def _event(update: UbwfrisIngestEvent, snapshot_id: str) -> StreamEvent:
    if isinstance(update, UbwfrisIngestProgress):
        status = UbwfrisIngestProgressStatus.PROGRESS
        kind = StreamEventKind.PROGRESS
        snapshot: str | None = None
        invalid_row_numbers: list[int] | None = None
    else:
        status = UbwfrisIngestProgressStatus.COMPLETE
        kind = StreamEventKind.COMPLETE
        snapshot = snapshot_id
        invalid_row_numbers = list(update.invalid_row_numbers)

    event = UbwfrisIngestProgressEvent(
        status=status,
        processed_rows=update.processed_rows,
        total_rows=update.total_rows,
        created_rows=update.created_rows,
        invalid_rows=update.invalid_rows,
        snapshot_id=snapshot,
        invalid_row_numbers=invalid_row_numbers,
    )
    return StreamEvent(
        event.status.value,
        event.model_dump(exclude_none=True),
        kind,
    )


def _failure_event(
    exc: Exception,
    snapshot_id: str,
    progress: UbwfrisIngestProgress | None,
) -> StreamEvent:
    error_status = 500
    detail = "UBWFRIS ingest failed"
    for error_type, status in UBWFRIS_ERROR_STATUS.items():
        if isinstance(exc, error_type):
            error_status = status
            detail = str(exc)
            break

    invalid_row_numbers: list[int] | None = (
        [] if progress is None or progress.invalid_rows == 0 else None
    )
    if isinstance(exc, InvalidWorkbook):
        invalid_row_numbers = list(exc.invalid_row_numbers)

    event = UbwfrisIngestProgressEvent(
        status=UbwfrisIngestProgressStatus.ERROR,
        snapshot_id=snapshot_id,
        processed_rows=progress.processed_rows if progress else 0,
        total_rows=progress.total_rows if progress else 0,
        created_rows=progress.created_rows if progress else 0,
        invalid_rows=progress.invalid_rows if progress else 0,
        invalid_row_numbers=invalid_row_numbers,
        error_status=error_status,
        detail=detail,
    )
    return StreamEvent(
        event.status.value,
        event.model_dump(exclude_none=True),
        StreamEventKind.ERROR,
    )


async def _upload_ubwfris_workbook[T: UbwfrisRow](
    file: UploadFile,
    parser: UbwfrisParser[T],
    writer: UbwfrisWriter[T],
) -> AsyncGenerator[ServerSentEvent]:
    workbook = UbwfrisWorkbook[T](await file.read())
    snapshot_id = sha256(workbook.file).hexdigest()
    last_progress: UbwfrisIngestProgress | None = None
    missing_outcome = _failure_event(
        ParserFailure("UBWFRIS ingest ended without an outcome"),
        snapshot_id,
        None,
    )
    lifecycle = StreamLifecycle(
        ProgressEventLimiter(config.stream_progress_interval_seconds),
        missing_outcome,
    )

    updates = ingest_ubwfris_workbook(
        UbwfrisIngestRequest(workbook), parser, writer, snapshot_id
    )

    try:
        async for update in updates:
            if isinstance(update, UbwfrisIngestProgress):
                last_progress = update
            response = lifecycle.encode(_event(update, snapshot_id))
            if response is not None:
                yield response
    except Exception as exc:  # noqa: BLE001 - terminate the SSE stream on unexpected failures
        response = lifecycle.encode(
            _failure_event(
                exc,
                snapshot_id,
                last_progress,
            )
        )
        if response is not None:
            yield response
    else:
        response = lifecycle.finish()
        if response is not None:
            yield response
    finally:
        with anyio.CancelScope(shield=True):
            await updates.aclose()


@router.post(
    "/ib",
    response_class=EventSourceResponse,
    summary="Import an IB workbook snapshot",
    description="Stream progress, then a complete or error event for the IB upload.",
)
async def upload_ib_workbook(
    file: Annotated[UploadFile, File(description="IB Excel workbook")],
    parser: Annotated[UbwfrisParser[IbRow], Depends(get_ubwfris_ib_parser)],
    writer: Annotated[UbwfrisWriter[IbRow], Depends(get_ubwfris_ib_writer)],
) -> AsyncGenerator[ServerSentEvent]:
    events = _upload_ubwfris_workbook(file, parser, writer)
    try:
        async for event in events:
            yield event
    finally:
        with anyio.CancelScope(shield=True):
            await events.aclose()


@router.post(
    "/rha",
    response_class=EventSourceResponse,
    summary="Import an RHA workbook snapshot",
    description="Stream progress, then a complete or error event for the RHA upload.",
)
async def upload_rha_workbook(
    file: Annotated[UploadFile, File(description="RHA Excel workbook")],
    parser: Annotated[UbwfrisParser[RhaRow], Depends(get_ubwfris_rha_parser)],
    writer: Annotated[UbwfrisWriter[RhaRow], Depends(get_ubwfris_rha_writer)],
) -> AsyncGenerator[ServerSentEvent]:
    events = _upload_ubwfris_workbook(file, parser, writer)
    try:
        async for event in events:
            yield event
    finally:
        with anyio.CancelScope(shield=True):
            await events.aclose()
