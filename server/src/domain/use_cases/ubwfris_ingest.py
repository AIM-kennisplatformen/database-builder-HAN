from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import date
from typing import assert_never, cast
from uuid import UUID

import anyio

from domain.exceptions.error import DomainError
from domain.exceptions.ubwfris_ingest_errors import (
    DuplicateUpload,
    InvalidWorkbook,
    ParserFailure,
    UbwfrisStoreUnavailable,
)
from domain.models.ubwfris import (
    IbRow,
    RhaRow,
    UbwfrisIngestEvent,
    UbwfrisIngestProgress,
    UbwfrisIngestRequest,
    UbwfrisIngestResult,
    UbwfrisIngestStatus,
    UbwfrisRow,
    UbwfrisWorkbook,
)
from domain.ports.ubwfris.ubwfris_parser import ParsedUbwfrisRow, UbwfrisParser
from domain.ports.ubwfris.ubwfris_store import UbwfrisWriter


async def _stream_ubwfris_parser[T: UbwfrisRow](
    parser: UbwfrisParser[T],
    workbook: UbwfrisWorkbook[T],
) -> AsyncIterator[ParsedUbwfrisRow[T]]:
    """Forward parsed workbook rows while preserving validation errors.

    Parser implementation errors are translated to ``ParserFailure`` so callers
    can distinguish malformed workbooks from parser infrastructure failures.
    """
    try:
        async for item in parser.stream_ubwfris_rows(workbook):
            yield item
    except InvalidWorkbook:
        raise
    except Exception as exc:
        raise ParserFailure("Failed to parse uploaded workbook") from exc


async def _stage_ubwfris_rows[T: UbwfrisRow](
    writer: UbwfrisWriter[T], snapshot_id: str, rows: list[T]
) -> int:
    """Stage a batch, preserving duplicate and workbook validation errors."""
    try:
        return await writer.stage_ubwfris_rows(snapshot_id, rows)
    except DuplicateUpload, InvalidWorkbook:
        raise
    except Exception as exc:
        raise UbwfrisStoreUnavailable(
            "Failed to save rows from the uploaded workbook"
        ) from exc


async def _begin_snapshot[T: UbwfrisRow](
    writer: UbwfrisWriter[T], snapshot_id: str
) -> None:
    """Create a snapshot, preserving duplicate-upload conflicts."""
    try:
        await writer.begin_snapshot(snapshot_id)
    except DuplicateUpload:
        raise
    except Exception as exc:
        raise UbwfrisStoreUnavailable("Failed to create the workbook snapshot") from exc


async def _begin_operation[T: UbwfrisRow](
    writer: UbwfrisWriter[T],
    operation_id: UUID,
    principal_id: str,
    snapshot_id: str,
) -> None:
    try:
        with anyio.CancelScope(shield=True):
            await writer.begin_operation(operation_id, principal_id, snapshot_id)
    except Exception as exc:
        raise UbwfrisStoreUnavailable("Failed to create the upload operation") from exc


async def _discard_snapshot[T: UbwfrisRow](
    writer: UbwfrisWriter[T], snapshot_id: str
) -> None:
    try:
        await writer.discard_snapshot(snapshot_id)
    except Exception as exc:
        raise UbwfrisStoreUnavailable("Failed to remove the incomplete snapshot") from exc


async def _publish_snapshot[T: UbwfrisRow](
    writer: UbwfrisWriter[T],
    snapshot_id: str,
    operation_id: UUID,
    summary: dict[str, object],
) -> None:
    try:
        await writer.publish_snapshot(snapshot_id, operation_id, summary)
    except Exception as exc:
        raise UbwfrisStoreUnavailable("Failed to complete the workbook snapshot") from exc


def _validate_ubwfris_row(row: UbwfrisRow) -> tuple[UbwfrisRow, bool]:
    """Normalize and validate a row using its workbook-specific rules."""
    match row:
        case IbRow():
            return _validate_ib_row(row)
        case RhaRow():
            return _validate_rha_row(row)
        case _:
            assert_never(row)


def _validate_ib_row(row: IbRow) -> tuple[IbRow, bool]:
    """Trim IB text fields and validate required values, row number, and hours."""
    normalized = replace(
        row,
        project_id=row.project_id.strip(),
        project_name=row.project_name.strip(),
        employee_name=row.employee_name.strip(),
        cost_center=row.cost_center.strip(),
    )
    valid = (
        normalized.row_number > 0
        and all(
            (
                normalized.project_id,
                normalized.project_name,
                normalized.employee_name,
                normalized.cost_center,
            )
        )
        and all(
            value.is_finite()
            for value in (
                normalized.budgeted_hours,
                normalized.projected_hours,
                normalized.booked_hours,
                normalized.remaining_hours,
            )
        )
    )
    return normalized, valid


def _validate_rha_row(row: RhaRow) -> tuple[RhaRow, bool]:
    """Trim RHA text fields and validate required values and typed measurements."""
    normalized = replace(
        row,
        employee_number=row.employee_number.strip(),
        employee_name=row.employee_name.strip(),
        project_manager=row.project_manager.strip(),
        project_id=row.project_id.strip(),
        project_status=row.project_status.strip(),
        project_name=row.project_name.strip(),
        subproject_id=row.subproject_id.strip(),
        subproject_status=row.subproject_status.strip(),
        subproject_name=row.subproject_name.strip(),
        cost_center=row.cost_center.strip(),
        project_type=row.project_type.strip(),
        main_department=row.main_department.strip(),
        academy=row.academy.strip(),
        main_department_name=row.main_department_name.strip(),
        employment_cost_center=row.employment_cost_center.strip(),
    )
    valid = (
        normalized.row_number > 0
        and all(
            (
                normalized.employee_number,
                normalized.employee_name,
                normalized.project_manager,
                normalized.project_id,
                normalized.project_status,
                normalized.project_name,
                normalized.subproject_id,
                normalized.subproject_status,
                normalized.subproject_name,
                normalized.cost_center,
                normalized.project_type,
                normalized.main_department,
                normalized.academy,
                normalized.main_department_name,
                normalized.employment_cost_center,
            )
        )
        and type(normalized.registration_date) is date
        and normalized.hours.is_finite()
        and (
            normalized.parttime_percentage is None
            or type(normalized.parttime_percentage) is int
        )
    )
    return normalized, valid


@dataclass
class _WorkbookProgress[T: UbwfrisRow]:
    """Accumulate row counts, duplicate tracking, and the current staging batch."""

    total_rows: int = 0
    processed_rows: int = 0
    created_rows: int = 0
    invalid_row_numbers: list[int] = field(default_factory=list)
    seen_row_numbers: set[int] = field(default_factory=set)
    batch: list[T] = field(default_factory=list)


async def _flush_workbook_row_batch[T: UbwfrisRow](
    writer: UbwfrisWriter[T], snapshot_id: str, progress: _WorkbookProgress[T]
) -> None:
    if not progress.batch:
        return
    progress.created_rows += await _stage_ubwfris_rows(
        writer, snapshot_id, progress.batch
    )
    progress.batch.clear()


async def _accept_workbook_row[T: UbwfrisRow](
    item: ParsedUbwfrisRow[T],
    writer: UbwfrisWriter[T],
    snapshot_id: str,
    progress: _WorkbookProgress[T],
) -> None:
    row_number = item.row_number
    if row_number in progress.seen_row_numbers:
        progress.invalid_row_numbers.append(row_number)
        return
    progress.seen_row_numbers.add(row_number)
    if item.row is None:
        progress.invalid_row_numbers.append(row_number)
        return
    normalized_row, is_valid = _validate_ubwfris_row(item.row)
    if not is_valid:
        progress.invalid_row_numbers.append(row_number)
        return
    progress.batch.append(cast(T, normalized_row))
    if len(progress.batch) >= 1000:
        await _flush_workbook_row_batch(writer, snapshot_id, progress)


async def _consume_workbook_rows[T: UbwfrisRow](
    parser: UbwfrisParser[T],
    writer: UbwfrisWriter[T],
    workbook: UbwfrisWorkbook[T],
    snapshot_id: str,
    progress: _WorkbookProgress[T],
) -> AsyncIterator[UbwfrisIngestProgress]:
    """Consume, validate, and stage rows, yielding progress as rows are parsed.

    The workbook is rejected if it has no rows, any invalid or duplicate row, or
    no valid rows to stage; callers therefore publish only wholly valid imports.
    """
    async for item in _stream_ubwfris_parser(parser, workbook):
        progress.total_rows = item.total_rows
        await _accept_workbook_row(item, writer, snapshot_id, progress)
        progress.processed_rows += 1
        yield UbwfrisIngestProgress(
            total_rows=progress.total_rows,
            processed_rows=progress.processed_rows,
            created_rows=progress.created_rows,
            invalid_row_count=len(progress.invalid_row_numbers),
        )
    await _flush_workbook_row_batch(writer, snapshot_id, progress)
    if progress.processed_rows == 0:
        raise InvalidWorkbook("The uploaded workbook is empty")
    if progress.invalid_row_numbers:
        raise InvalidWorkbook(
            "Workbook contains invalid rows",
            invalid_row_numbers=tuple(progress.invalid_row_numbers),
        )
    if progress.created_rows == 0:
        raise InvalidWorkbook(
            "The uploaded workbook has no valid rows",
            invalid_row_numbers=tuple(progress.invalid_row_numbers),
        )


async def _handle_ingest_failure[T: UbwfrisRow](
    exc: BaseException,
    writer: UbwfrisWriter[T],
    snapshot_id: str,
    operation_id: UUID,
    snapshot_started: bool,
    operation_started: bool,
    progress: _WorkbookProgress[T],
) -> None:
    interrupted = isinstance(exc, anyio.get_cancelled_exc_class() | GeneratorExit)
    if isinstance(exc, InvalidWorkbook):
        progress.invalid_row_numbers = list(
            dict.fromkeys((*progress.invalid_row_numbers, *exc.invalid_row_numbers))
        )
    failure_code, reason = _ubwfris_failure(exc, interrupted)
    failure_summary: dict[str, object] = {
        "total_rows": progress.total_rows,
        "processed_rows": progress.processed_rows,
        "staged_rows": progress.created_rows,
        "validated_rows": progress.created_rows + len(progress.batch),
        "published_rows": 0,
        "invalid_rows": len(progress.invalid_row_numbers),
        "invalid_row_numbers": progress.invalid_row_numbers,
        "reason": reason,
    }
    if isinstance(exc, DomainError):
        exc.failure_summary = failure_summary
    try:
        if snapshot_started:
            try:
                with anyio.CancelScope(shield=True):
                    await _discard_snapshot(writer, snapshot_id)
            except BaseException as cleanup_exc:
                if isinstance(cleanup_exc, DomainError):
                    cleanup_exc.failure_summary = failure_summary
                raise
    finally:
        if operation_started:
            with anyio.CancelScope(shield=True):
                await writer.fail_operation(
                    operation_id,
                    failure_code,
                    failure_summary,
                    interrupted=interrupted,
                )


async def ingest_ubwfris_workbook[T: UbwfrisRow](
    req: UbwfrisIngestRequest[T],
    parser: UbwfrisParser[T],
    writer: UbwfrisWriter[T],
    snapshot_id: str,
    operation_id: UUID,
    principal_id: str,
) -> AsyncGenerator[UbwfrisIngestEvent]:
    """Validate a workbook and publish its staged rows as one snapshot.

    Rows are staged in batches, but the snapshot is visible only after every row
    passes validation. Any failure discards the incomplete snapshot and records
    the failed or interrupted operation.

    Args:
        req: Workbook and workbook-specific row model.
        parser: Parser that streams rows from the workbook.
        writer: Store that stages and publishes the snapshot.
        snapshot_id: Stable identifier used for duplicate-upload detection.
        operation_id: Identifier for the upload operation record.
        principal_id: Principal associated with the operation.

    Yields:
        Row progress events followed by the completed ingest result.

    Raises:
        InvalidWorkbook: If the workbook is empty or contains invalid rows.
        DuplicateUpload: If this snapshot has already been imported.
        ParserFailure: If parsing fails for an infrastructure reason.
        UbwfrisStoreUnavailable: If persistence fails.
    """
    progress = _WorkbookProgress[T]()
    completed = False
    operation_started = False
    snapshot_started = False
    try:
        await _begin_operation(writer, operation_id, principal_id, snapshot_id)
        operation_started = True
        if not req.workbook.file:
            raise InvalidWorkbook("The uploaded workbook is empty")

        await _begin_snapshot(writer, snapshot_id)
        snapshot_started = True
        async for progress_event in _consume_workbook_rows(
            parser, writer, req.workbook, snapshot_id, progress
        ):
            yield progress_event

        await anyio.lowlevel.checkpoint()
        summary: dict[str, object] = {
            "total_rows": progress.total_rows,
            "processed_rows": progress.processed_rows,
            "created_rows": progress.created_rows,
            "invalid_rows": 0,
            "invalid_row_numbers": [],
        }
        await _publish_snapshot(writer, snapshot_id, operation_id, summary)
        completed = True
        yield UbwfrisIngestResult(
            status=UbwfrisIngestStatus.COMPLETED,
            total_rows=progress.total_rows,
            processed_rows=progress.processed_rows,
            created_rows=progress.created_rows,
            invalid_row_count=len(progress.invalid_row_numbers),
            invalid_row_numbers=tuple(progress.invalid_row_numbers),
        )
    except BaseException as exc:
        if completed:
            raise
        await _handle_ingest_failure(
            exc,
            writer,
            snapshot_id,
            operation_id,
            snapshot_started,
            operation_started,
            progress,
        )
        raise


def _ubwfris_failure(exc: BaseException, interrupted: bool) -> tuple[str, str]:
    """Map an ingest error to its durable failure code and human-readable reason."""
    if interrupted:
        return "request_interrupted", "Upload was interrupted"
    if isinstance(exc, InvalidWorkbook):
        return "ubwfris.invalid_workbook", "Workbook is invalid"
    if isinstance(exc, DuplicateUpload):
        return "ubwfris.duplicate_upload", "This workbook was already imported"
    if isinstance(exc, ParserFailure):
        return "ubwfris.parser_failure", "Workbook parsing failed"
    if isinstance(exc, UbwfrisStoreUnavailable):
        return "ubwfris.store_unavailable", "UBWFRIS store is unavailable"
    return "ubwfris.ingest_failed", "UBWFRIS upload failed"
