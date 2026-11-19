import asyncio
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from typing import override

import pytest
from fastapi import UploadFile

from api.routes.ubwfris.snapshots import ingest as ubwfris_route
from api.routes.ubwfris.snapshots.ingest import upload_ib_workbook
from domain.exceptions.ubwfris_ingest_errors import DuplicateUpload, InvalidWorkbook
from domain.models.ubwfris import (
    IbRow,
    UbwfrisIngestEvent,
    UbwfrisIngestProgress,
    UbwfrisIngestRequest,
    UbwfrisWorkbook,
)
from domain.ports.ubwfris.ubwfris_parser import ParsedUbwfrisRow, UbwfrisParser
from domain.ports.ubwfris.ubwfris_store import UbwfrisWriter


class Parser:
    async def stream_ubwfris_rows(
        self, workbook: UbwfrisWorkbook[IbRow]
    ) -> AsyncIterator[ParsedUbwfrisRow[IbRow]]:
        assert workbook.file == b"workbook"
        yield ParsedUbwfrisRow(
            IbRow(
                row_number=1,
                project_id="project",
                project_name="Project",
                employee_name="Employee",
                budgeted_hours=Decimal(10),
                projected_hours=Decimal(8),
                booked_hours=Decimal(4),
                period=202601,
                remaining_hours=Decimal(6),
                cost_center="center",
            ),
            row_number=1,
            total_rows=1,
        )


class Writer:
    def __init__(self) -> None:
        self.snapshot_id: str | None = None
        self.rows: list[IbRow] = []

    async def begin_snapshot(self, snapshot_id: str) -> None:
        self.snapshot_id = snapshot_id

    async def write_ubwfris_rows(self, snapshot_id: str, rows: Sequence[IbRow]) -> int:
        assert snapshot_id == self.snapshot_id
        self.rows.extend(rows)
        return len(rows)

    async def discard_snapshot(self, snapshot_id: str) -> None:
        assert snapshot_id == self.snapshot_id
        self.rows.clear()


def test_ubwfris_stream_emits_progress_then_one_complete() -> None:
    async def collect():
        writer = Writer()
        events = [
            event
            async for event in upload_ib_workbook(
                UploadFile(filename="upload.xlsx", file=BytesIO(b"workbook")),
                Parser(),
                writer,
            )
        ]
        return events, writer

    events, writer = asyncio.run(collect())

    assert [event.event for event in events] == ["progress", "complete"]
    assert sum(event.event in {"complete", "error"} for event in events) == 1
    assert events[-1].data["snapshot_id"] == writer.snapshot_id
    assert events[-1].data["processed_rows"] == 1
    assert events[-1].data["created_rows"] == 1
    assert len(writer.rows) == 1


def test_ubwfris_stream_preserves_invalid_workbook_error_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ubwfris_route,
        "config",
        SimpleNamespace(stream_progress_interval_seconds=1),
    )

    async def collect():
        return [
            event
            async for event in upload_ib_workbook(
                UploadFile(filename="empty.xlsx", file=BytesIO(b"")),
                Parser(),
                Writer(),
            )
        ]

    events = asyncio.run(collect())

    assert [event.event for event in events] == ["error"]
    assert events[0].data == {
        "status": "error",
        "error": {
            "code": "ubwfris.invalid_workbook",
            "http_status": 422,
            "detail": "The uploaded workbook is empty",
        },
        "context": {
            "operation": "ubwfris.ingest",
            "snapshot_id": sha256(b"").hexdigest(),
        },
        "progress": {
            "processed_rows": 0,
            "total_rows": 0,
            "created_rows": 0,
            "invalid_rows": 0,
            "invalid_row_numbers": [],
        },
    }


def test_ubwfris_stream_preserves_duplicate_upload_error_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ubwfris_route,
        "config",
        SimpleNamespace(stream_progress_interval_seconds=1),
    )

    class DuplicateWriter(Writer):
        @override
        async def begin_snapshot(self, snapshot_id: str) -> None:
            _ = snapshot_id
            raise DuplicateUpload("Workbook was already imported")

    async def collect():
        return [
            event
            async for event in upload_ib_workbook(
                UploadFile(filename="upload.xlsx", file=BytesIO(b"workbook")),
                Parser(),
                DuplicateWriter(),
            )
        ]

    events = asyncio.run(collect())

    assert [event.event for event in events] == ["error"]
    assert events[0].data == {
        "status": "error",
        "error": {
            "code": "ubwfris.duplicate_upload",
            "http_status": 409,
            "detail": "Workbook was already imported",
        },
        "context": {
            "operation": "ubwfris.ingest",
            "snapshot_id": sha256(b"workbook").hexdigest(),
        },
        "progress": {
            "processed_rows": 0,
            "total_rows": 0,
            "created_rows": 0,
            "invalid_rows": 0,
            "invalid_row_numbers": [],
        },
    }


def test_ubwfris_stream_masks_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setattr(
        ubwfris_route,
        "config",
        SimpleNamespace(stream_progress_interval_seconds=1),
    )

    async def broken_ingest(
        request: UbwfrisIngestRequest[IbRow],
        parser: UbwfrisParser[IbRow],
        writer: UbwfrisWriter[IbRow],
        snapshot_id: str,
    ) -> AsyncIterator[UbwfrisIngestEvent]:
        _ = (request, parser, writer, snapshot_id)
        yield UbwfrisIngestProgress(2, 1, 1, 0)
        raise RuntimeError("database password must not be exposed")

    monkeypatch.setattr(ubwfris_route, "ingest_ubwfris_workbook", broken_ingest)

    async def collect():
        return [
            event
            async for event in upload_ib_workbook(
                UploadFile(filename="upload.xlsx", file=BytesIO(b"workbook")),
                Parser(),
                Writer(),
            )
        ]

    events = asyncio.run(collect())

    assert [event.event for event in events] == ["progress", "error"]
    assert events[-1].data == {
        "status": "error",
        "error": {
            "code": "internal_error",
            "http_status": 500,
            "detail": "The operation failed",
        },
        "context": {
            "operation": "ubwfris.ingest",
            "snapshot_id": sha256(b"workbook").hexdigest(),
        },
        "progress": {
            "processed_rows": 1,
            "total_rows": 2,
            "created_rows": 1,
            "invalid_rows": 0,
            "invalid_row_numbers": [],
        },
    }
    assert "database password" not in str(events[-1].data)
    failure_records = [record for record in caplog.records if record.exc_info]
    assert len(failure_records) == 1
    assert failure_records[0].exc_info is not None
    assert isinstance(failure_records[0].exc_info[1], RuntimeError)
    assert sum(record.name == "api.errors" for record in caplog.records) == 1
    assert "database password" in caplog.text


def test_ubwfris_known_error_subclass_inherits_status_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setattr(
        ubwfris_route,
        "config",
        SimpleNamespace(stream_progress_interval_seconds=1),
    )

    class SpecializedInvalidWorkbook(InvalidWorkbook):
        pass

    async def broken_ingest(
        request: UbwfrisIngestRequest[IbRow],
        parser: UbwfrisParser[IbRow],
        writer: UbwfrisWriter[IbRow],
        snapshot_id: str,
    ) -> AsyncIterator[UbwfrisIngestEvent]:
        _ = (request, parser, writer, snapshot_id)
        yield UbwfrisIngestProgress(2, 1, 1, 1)
        raise SpecializedInvalidWorkbook(
            "workbook subclass failure", invalid_row_numbers=(2,)
        )

    monkeypatch.setattr(ubwfris_route, "ingest_ubwfris_workbook", broken_ingest)

    async def collect():
        return [
            event
            async for event in upload_ib_workbook(
                UploadFile(filename="upload.xlsx", file=BytesIO(b"workbook")),
                Parser(),
                Writer(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["progress", "error"]
    assert events[-1].data["error"]["code"] == "ubwfris.invalid_workbook"
    assert events[-1].data["error"]["http_status"] == 422
    assert events[-1].data["progress"]["invalid_row_numbers"] == [2]
    assert "traceback" not in events[-1].data
