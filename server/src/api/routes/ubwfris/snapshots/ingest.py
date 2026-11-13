"""Upload IB and RHA workbooks as UBWFRIS snapshots."""

from collections.abc import AsyncGenerator, AsyncIterator
from enum import StrEnum
from hashlib import sha256
from traceback import format_exception
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from api.progress import ProgressEventLimiter
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
from domain.ports.persistence.ubwfris_store import UbwfrisWriter
from domain.ports.sources.ubwfris_parser import UbwfrisParser
from domain.use_cases.ubwfris_ingest import ingest_ubwfris_workbook

router = APIRouter()

type IngestError = InvalidWorkbook | DuplicateUpload | ParserFailure | StoreUnavailable


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
    traceback: str | None = None


def _sse_event(progress: UbwfrisIngestProgressEvent) -> ServerSentEvent:
    return ServerSentEvent(
        event=progress.status.value,
        data=progress.model_dump(exclude_none=True),
    )


def _error_status(exc: IngestError) -> int:
    if isinstance(exc, InvalidWorkbook):
        return 422
    if isinstance(exc, DuplicateUpload):
        return 409
    if isinstance(exc, StoreUnavailable):
        return 503
    return 500


def _event(update: UbwfrisIngestEvent, snapshot_id: str) -> ServerSentEvent:
    if isinstance(update, UbwfrisIngestProgress):
        return _sse_event(
            UbwfrisIngestProgressEvent(
                status=UbwfrisIngestProgressStatus.PROGRESS,
                processed_rows=update.processed_rows,
                total_rows=update.total_rows,
                created_rows=update.created_rows,
                invalid_rows=update.invalid_rows,
            )
        )
    return _sse_event(
        UbwfrisIngestProgressEvent(
            status=UbwfrisIngestProgressStatus.COMPLETE,
            snapshot_id=snapshot_id,
            processed_rows=update.processed_rows,
            total_rows=update.total_rows,
            created_rows=update.created_rows,
            invalid_rows=update.invalid_rows,
            invalid_row_numbers=list(update.invalid_row_numbers),
        )
    )


def _failure_event(
    exc: IngestError,
    snapshot_id: str,
    progress: UbwfrisIngestProgress | None,
    *,
    traceback_exc: BaseException | None = None,
) -> ServerSentEvent:
    invalid_row_numbers: list[int] | None = (
        [] if progress is None or progress.invalid_rows == 0 else None
    )
    if isinstance(exc, InvalidWorkbook):
        invalid_row_numbers = list(exc.invalid_row_numbers)
    return _sse_event(
        UbwfrisIngestProgressEvent(
            status=UbwfrisIngestProgressStatus.ERROR,
            snapshot_id=snapshot_id,
            processed_rows=progress.processed_rows if progress else 0,
            total_rows=progress.total_rows if progress else 0,
            created_rows=progress.created_rows if progress else 0,
            invalid_rows=progress.invalid_rows if progress else 0,
            invalid_row_numbers=invalid_row_numbers,
            error_status=_error_status(exc),
            detail=str(exc),
            traceback="".join(format_exception(traceback_exc or exc))
            if config.debug
            else None,
        )
    )


async def _upload_ubwfris_workbook[T: UbwfrisRow](
    file: UploadFile,
    parser: UbwfrisParser[T],
    writer: UbwfrisWriter[T],
) -> AsyncGenerator[ServerSentEvent]:
    workbook = UbwfrisWorkbook[T](await file.read())
    snapshot_id = sha256(workbook.file).hexdigest()
    last_progress: UbwfrisIngestProgress | None = None
    progress_limiter = ProgressEventLimiter(config.stream_progress_interval_seconds)

    updates = ingest_ubwfris_workbook(
        UbwfrisIngestRequest(workbook), parser, writer, snapshot_id
    )
    try:
        try:
            async for update in updates:
                if isinstance(update, UbwfrisIngestProgress):
                    last_progress = update
                    if not progress_limiter.should_emit():
                        continue
                yield _event(update, snapshot_id)
        except (
            InvalidWorkbook,
            DuplicateUpload,
            ParserFailure,
            StoreUnavailable,
        ) as exc:
            yield _failure_event(exc, snapshot_id, last_progress)
        except Exception as exc:  # noqa: BLE001 - terminate the SSE stream on unexpected failures
            yield _failure_event(
                ParserFailure("UBWFRIS ingest failed"),
                snapshot_id,
                last_progress,
                traceback_exc=exc,
            )
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
) -> AsyncIterator[ServerSentEvent]:
    async for event in _upload_ubwfris_workbook(file, parser, writer):
        yield event


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
) -> AsyncIterator[ServerSentEvent]:
    async for event in _upload_ubwfris_workbook(file, parser, writer):
        yield event
