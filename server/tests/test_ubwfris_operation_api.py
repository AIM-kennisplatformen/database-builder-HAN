import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast, override
from uuid import UUID

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse

from api.routes.operations import OperationRunResponse, get_operation_run
from api.routes.ubwfris.snapshots.ingest import (
    UbwfrisIngestResponse,
    upload_ib_workbook,
    upload_rha_workbook,
)
from domain.models.ubwfris import IbRow, RhaRow, UbwfrisRow, UbwfrisWorkbook
from domain.ports.operation_run_store import OperationRun
from domain.ports.ubwfris.ubwfris_parser import ParsedUbwfrisRow


def _ib_row(row_number: int = 1, *, valid: bool = True) -> IbRow:
    return IbRow(
        row_number=row_number,
        project_id="project" if valid else "",
        project_name="Project",
        employee_name="Employee",
        budgeted_hours=Decimal(10),
        projected_hours=Decimal(8),
        booked_hours=Decimal(4),
        period=202601,
        remaining_hours=Decimal(6),
        cost_center="center",
    )


def _rha_row() -> RhaRow:
    from datetime import date

    return RhaRow(
        row_number=1,
        employee_number="1",
        employee_name="Employee",
        project_manager="Manager",
        project_id="project",
        project_status="active",
        project_name="Project",
        subproject_id="subproject",
        subproject_status="active",
        subproject_name="Subproject",
        cost_center="center",
        project_type="type",
        main_department="department",
        academy="academy",
        main_department_name="Department",
        employment_cost_center="center",
        registration_date=date(2026, 1, 1),
        hours=Decimal(8),
        parttime_percentage=100,
    )


class FakeParser[T: UbwfrisRow]:
    def __init__(self, rows: Sequence[T], *, broken: bool = False) -> None:
        self.rows: list[T] = list(rows)
        self.broken: bool = broken

    async def stream_ubwfris_rows(
        self, workbook: UbwfrisWorkbook[T]
    ) -> AsyncIterator[ParsedUbwfrisRow[T]]:
        assert workbook.file == b"same-workbook"
        for index, row in enumerate(self.rows, start=1):
            yield ParsedUbwfrisRow(row, row_number=index, total_rows=len(self.rows))
        if self.broken:
            raise RuntimeError("private parser exception")


class FakeWriter[T: UbwfrisRow]:
    kind: str

    def __init__(self, kind: str = "ubwfris.ib") -> None:
        self.kind = kind
        self.operations: dict[UUID, OperationRun] = {}
        self.owners: dict[UUID, str] = {}
        self.staged: dict[str, list[T]] = {}
        self.published: set[str] = set()
        self.published_rows: list[T] = []

    async def begin_operation(
        self, operation_id: UUID, principal_id: str, snapshot_id: str
    ) -> None:
        self.operations[operation_id] = OperationRun(
            id=operation_id,
            kind=self.kind,
            status="running",
            started_at=datetime.now(UTC),
            finished_at=None,
            summary={},
            failure_code=None,
            domain_ids={"snapshot_id": snapshot_id, "row_type": self.kind.removeprefix("ubwfris.")},
        )
        self.owners[operation_id] = principal_id

    async def begin_snapshot(self, snapshot_id: str) -> None:
        if snapshot_id in self.published:
            from domain.exceptions.ubwfris_ingest_errors import DuplicateUpload

            raise DuplicateUpload("duplicate")
        self.staged[snapshot_id] = []

    async def stage_ubwfris_rows(self, snapshot_id: str, rows: Sequence[T]) -> int:
        self.staged[snapshot_id].extend(rows)
        return len(rows)

    async def publish_snapshot(
        self, snapshot_id: str, operation_id: UUID, summary: dict[str, object]
    ) -> None:
        self.published_rows.extend(self.staged.pop(snapshot_id))
        self.published.add(snapshot_id)
        self._finish(operation_id, "complete", summary, None)

    async def discard_snapshot(self, snapshot_id: str) -> None:
        self.staged.pop(snapshot_id, None)

    async def fail_operation(
        self,
        operation_id: UUID,
        failure_code: str,
        summary: dict[str, object],
        *,
        interrupted: bool = False,
    ) -> None:
        self._finish(
            operation_id,
            "interrupted" if interrupted else "failed",
            summary,
            failure_code,
        )

    def _finish(
        self,
        operation_id: UUID,
        status: str,
        summary: dict[str, object],
        failure_code: str | None,
    ) -> None:
        run = self.operations[operation_id]
        self.operations[operation_id] = OperationRun(
            id=run.id,
            kind=run.kind,
            status=status,
            started_at=run.started_at,
            finished_at=datetime.now(UTC),
            summary=summary,
            failure_code=failure_code,
            domain_ids=run.domain_ids,
        )

    async def get_operation_run(
        self, operation_id: UUID, principal_id: str
    ) -> OperationRun | None:
        run = self.operations.get(operation_id)
        if run is None or self.owners[operation_id] != principal_id:
            return None
        return run


class MemoryUpload:
    async def read(self) -> bytes:
        return b"same-workbook"


def _file() -> UploadFile:
    return cast(UploadFile, cast(object, MemoryUpload()))


type UploadResponse = UbwfrisIngestResponse | JSONResponse


def _json_body(response: UploadResponse) -> dict[str, Any]:
    if isinstance(response, JSONResponse):
        return json.loads(bytes(response.body))
    return response.model_dump(mode="json")


def _assert_operation_owner_scoped(
    operation_id: UUID, writer: FakeWriter[UbwfrisRow]
) -> OperationRunResponse:
    async def read_status() -> tuple[OperationRunResponse, HTTPException]:
        owned = await get_operation_run(operation_id, "owner", writer)
        with pytest.raises(HTTPException) as hidden_error:
            await get_operation_run(operation_id, "other", writer)
        return owned, hidden_error.value

    owned, hidden = asyncio.run(read_status())
    assert hidden.status_code == 404
    return owned


def test_ib_upload_returns_json_result_and_owner_scoped_operation() -> None:
    writer = FakeWriter[IbRow]("ubwfris.ib")

    async def upload():
        return await upload_ib_workbook(_file(), "owner", FakeParser([_ib_row()]), writer)

    response = asyncio.run(upload())
    assert isinstance(response, UbwfrisIngestResponse)
    assert response.row_type == "ib"
    assert response.created_rows == 1
    assert len(writer.published_rows) == 1
    owned = _assert_operation_owner_scoped(response.operation_id, cast(FakeWriter[UbwfrisRow], writer))
    assert owned.status == "complete"
    assert owned.kind == "ubwfris.ib"
    assert owned.id == response.operation_id
    assert owned.domain_ids == {"snapshot_id": response.snapshot_id, "row_type": "ib"}


def test_rha_upload_returns_json_result_and_owner_scoped_operation() -> None:
    writer = FakeWriter[RhaRow]("ubwfris.rha")

    async def upload():
        return await upload_rha_workbook(_file(), "owner", FakeParser([_rha_row()]), writer)

    response = asyncio.run(upload())
    assert isinstance(response, UbwfrisIngestResponse)
    assert response.row_type == "rha"
    assert response.created_rows == 1
    assert len(writer.published_rows) == 1
    owned = _assert_operation_owner_scoped(response.operation_id, cast(FakeWriter[UbwfrisRow], writer))
    assert owned.status == "complete"
    assert owned.kind == "ubwfris.rha"
    assert owned.id == response.operation_id
    assert owned.domain_ids == {"snapshot_id": response.snapshot_id, "row_type": "rha"}


def test_duplicate_upload_returns_distinct_failed_operation_and_keeps_published_rows() -> None:
    writer = FakeWriter[IbRow]()

    async def exercise():
        first = await upload_ib_workbook(_file(), "owner", FakeParser([_ib_row()]), writer)
        duplicate = await upload_ib_workbook(
            _file(), "owner", FakeParser([_ib_row()]), writer
        )
        duplicate_body = _json_body(duplicate)
        status = await get_operation_run(
            UUID(cast(str, duplicate_body["operation_id"])), "owner", writer
        )
        return first, duplicate, status

    first, duplicate, status = asyncio.run(exercise())
    duplicate_body = _json_body(duplicate)
    assert isinstance(first, UbwfrisIngestResponse)
    assert isinstance(duplicate, JSONResponse)
    assert first.operation_id != UUID(cast(str, duplicate_body["operation_id"]))
    assert duplicate.status_code == 409
    assert duplicate_body["error"]["code"] == "ubwfris.duplicate_upload"
    assert status.status == "failed"
    assert status.failure_code == "ubwfris.duplicate_upload"
    assert len(writer.published_rows) == 1


def test_mixed_valid_invalid_rows_fail_atomically_with_safe_row_summary_and_retry() -> None:
    writer = FakeWriter[IbRow]()

    async def exercise():
        failed = await upload_ib_workbook(
            _file(), "owner", FakeParser([_ib_row(1), _ib_row(2, valid=False)]), writer
        )
        retried = await upload_ib_workbook(
            _file(), "owner", FakeParser([_ib_row()]), writer
        )
        status = await get_operation_run(
            UUID(cast(str, _json_body(failed)["operation_id"])), "owner", writer
        )
        return failed, retried, status

    failed, retried, status = asyncio.run(exercise())
    assert isinstance(failed, JSONResponse)
    assert isinstance(retried, UbwfrisIngestResponse)
    assert failed.status_code == 422
    failed_body = _json_body(failed)
    assert failed_body["summary"]["invalid_row_numbers"] == [2]
    assert failed_body["summary"]["staged_rows"] == 1
    assert failed_body["summary"]["published_rows"] == 0
    assert "private" not in str(failed_body)
    assert retried.created_rows == 1
    assert status.status == "failed"
    assert status.summary["invalid_row_numbers"] == [2]
    assert status.summary["staged_rows"] == 1
    assert status.summary["published_rows"] == 0
    assert len(writer.published_rows) == 1


def test_parser_failure_is_safe_and_same_bytes_can_be_retried() -> None:
    writer = FakeWriter[IbRow]()

    async def exercise():
        failed = await upload_ib_workbook(
            _file(), "owner", FakeParser([_ib_row()], broken=True), writer
        )
        retried = await upload_ib_workbook(_file(), "owner", FakeParser([_ib_row()]), writer)
        return failed, retried

    failed, retried = asyncio.run(exercise())
    assert isinstance(failed, JSONResponse)
    assert isinstance(retried, UbwfrisIngestResponse)
    assert failed.status_code == 500
    assert "private parser exception" not in str(_json_body(failed))
    assert retried.created_rows == 1
    assert len(writer.published_rows) == 1


def test_cancelled_ingest_persists_interrupted_operation() -> None:
    import anyio

    writer = FakeWriter[IbRow]()
    operation_id = UUID("00000000-0000-0000-0000-000000000123")

    class BlockingParser(FakeParser[IbRow]):
        @override
        async def stream_ubwfris_rows(
            self, workbook: UbwfrisWorkbook[IbRow]
        ) -> AsyncIterator[ParsedUbwfrisRow[IbRow]]:
            yield ParsedUbwfrisRow(_ib_row(), row_number=1, total_rows=1)
            await anyio.sleep_forever()

    from domain.models.ubwfris import UbwfrisIngestRequest
    from domain.use_cases.ubwfris_ingest import ingest_ubwfris_workbook

    async def exercise() -> None:
        updates = ingest_ubwfris_workbook(
            UbwfrisIngestRequest(UbwfrisWorkbook[IbRow](b"same-workbook")),
            BlockingParser([_ib_row()]),
            writer,
            "snapshot",
            operation_id,
            "owner",
        )
        await anext(updates)
        await updates.aclose()

    asyncio.run(exercise())
    run = writer.operations[operation_id]
    assert run.status == "interrupted"
    assert run.failure_code == "request_interrupted"
    assert run.summary["processed_rows"] == 1
