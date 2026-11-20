from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import replace
from datetime import date
from typing import assert_never, cast
from uuid import UUID

import anyio

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
    try:
        return await writer.stage_ubwfris_rows(snapshot_id, rows)
    except (DuplicateUpload, InvalidWorkbook):
        raise
    except Exception as exc:
        raise StoreUnavailable(
            "Failed to save rows from the uploaded workbook"
        ) from exc


async def _begin_snapshot[T: UbwfrisRow](
    writer: UbwfrisWriter[T], snapshot_id: str
) -> None:
    try:
        await writer.begin_snapshot(snapshot_id)
    except DuplicateUpload:
        raise
    except Exception as exc:
        raise StoreUnavailable("Failed to create the workbook snapshot") from exc


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
        raise StoreUnavailable("Failed to create the upload operation") from exc


async def _discard_snapshot[T: UbwfrisRow](
    writer: UbwfrisWriter[T], snapshot_id: str
) -> None:
    try:
        await writer.discard_snapshot(snapshot_id)
    except Exception as exc:
        raise StoreUnavailable("Failed to remove the incomplete snapshot") from exc


async def _publish_snapshot[T: UbwfrisRow](
    writer: UbwfrisWriter[T],
    snapshot_id: str,
    operation_id: UUID,
    summary: dict[str, object],
) -> None:
    try:
        await writer.publish_snapshot(snapshot_id, operation_id, summary)
    except Exception as exc:
        raise StoreUnavailable("Failed to complete the workbook snapshot") from exc


def _validate_ubwfris_row(row: UbwfrisRow) -> tuple[UbwfrisRow, bool]:
    match row:
        case IbRow():
            return _validate_ib_row(row)
        case RhaRow():
            return _validate_rha_row(row)
        case _:
            assert_never(row)


def _validate_ib_row(row: IbRow) -> tuple[IbRow, bool]:
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


async def ingest_ubwfris_workbook[T: UbwfrisRow](
    req: UbwfrisIngestRequest[T],
    parser: UbwfrisParser[T],
    writer: UbwfrisWriter[T],
    snapshot_id: str,
    operation_id: UUID,
    principal_id: str,
) -> AsyncGenerator[UbwfrisIngestEvent]:
    """Validate and stage a workbook, then publish all rows in one transaction."""
    total_rows = 0
    processed_rows = 0
    created_rows = 0
    invalid_row_numbers: list[int] = []
    seen_row_numbers: set[int] = set()
    batch: list[T] = []

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
        async for item in _stream_ubwfris_parser(parser, req.workbook):
            row = item.row
            row_number = item.row_number
            total_rows = item.total_rows
            if row_number in seen_row_numbers:
                invalid_row_numbers.append(row_number)
            elif row is None:
                seen_row_numbers.add(row_number)
                invalid_row_numbers.append(row_number)
            else:
                seen_row_numbers.add(row_number)
                normalized_row, is_valid = _validate_ubwfris_row(row)
                if not is_valid:
                    invalid_row_numbers.append(row_number)
                else:
                    batch.append(cast(T, normalized_row))
                    if len(batch) >= 1000:
                        created_rows += await _stage_ubwfris_rows(
                            writer, snapshot_id, batch
                        )
                        batch.clear()

            processed_rows += 1
            yield UbwfrisIngestProgress(
                total_rows=total_rows,
                processed_rows=processed_rows,
                created_rows=created_rows,
                invalid_rows=len(invalid_row_numbers),
            )

        if batch:
            created_rows += await _stage_ubwfris_rows(writer, snapshot_id, batch)
            batch.clear()

        if processed_rows == 0:
            raise InvalidWorkbook("The uploaded workbook is empty")
        if invalid_row_numbers:
            raise InvalidWorkbook(
                "Workbook contains invalid rows",
                invalid_row_numbers=tuple(invalid_row_numbers),
            )
        if created_rows == 0:
            raise InvalidWorkbook(
                "The uploaded workbook has no valid rows",
                invalid_row_numbers=tuple(invalid_row_numbers),
            )

        await anyio.lowlevel.checkpoint()
        summary: dict[str, object] = {
            "total_rows": total_rows,
            "processed_rows": processed_rows,
            "created_rows": created_rows,
            "invalid_rows": 0,
            "invalid_row_numbers": [],
        }
        await _publish_snapshot(writer, snapshot_id, operation_id, summary)
        completed = True
        yield UbwfrisIngestResult(
            status=UbwfrisIngestStatus.COMPLETED,
            total_rows=total_rows,
            processed_rows=processed_rows,
            created_rows=created_rows,
            invalid_rows=len(invalid_row_numbers),
            invalid_row_numbers=tuple(invalid_row_numbers),
        )
    except BaseException as exc:
        if completed:
            raise
        interrupted = isinstance(exc, anyio.get_cancelled_exc_class() | GeneratorExit)
        if isinstance(exc, InvalidWorkbook):
            invalid_row_numbers = list(
                dict.fromkeys((*invalid_row_numbers, *exc.invalid_row_numbers))
            )
        failure_code, reason = _ubwfris_failure(exc, interrupted)
        failure_summary: dict[str, object] = {
            "total_rows": total_rows,
            "processed_rows": processed_rows,
            "staged_rows": created_rows,
            "validated_rows": created_rows + len(batch),
            "published_rows": 0,
            "invalid_rows": len(invalid_row_numbers),
            "invalid_row_numbers": invalid_row_numbers,
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
        raise


def _ubwfris_failure(exc: BaseException, interrupted: bool) -> tuple[str, str]:
    if interrupted:
        return "request_interrupted", "Upload was interrupted"
    if isinstance(exc, InvalidWorkbook):
        return "ubwfris.invalid_workbook", "Workbook is invalid"
    if isinstance(exc, DuplicateUpload):
        return "ubwfris.duplicate_upload", "This workbook was already imported"
    if isinstance(exc, ParserFailure):
        return "ubwfris.parser_failure", "Workbook parsing failed"
    if isinstance(exc, StoreUnavailable):
        return "ubwfris.store_unavailable", "UBWFRIS store is unavailable"
    return "ubwfris.ingest_failed", "UBWFRIS upload failed"
