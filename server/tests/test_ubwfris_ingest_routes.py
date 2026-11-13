"""Exercise both workbook upload routes through their HTTP and SSE contracts."""

import asyncio
import json
from collections.abc import Sequence
from datetime import date
from hashlib import sha256
from io import BytesIO
from typing import override

import anyio
import pytest
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient, Response
from openpyxl import Workbook

from api.routes.ubwfris.snapshots.ingest import (
    _upload_ubwfris_workbook,  # pyright: ignore[reportPrivateUsage]
)
from api.routes.ubwfris.snapshots.router import router
from config import config
from dependencies import get_ubwfris_ib_writer, get_ubwfris_rha_writer
from domain.exceptions.ubwfris_ingest_errors import DuplicateUpload
from domain.models.ubwfris import UbwfrisRow
from infrastructure.sources.ubwfris_excel_parser import UbwfrisIbExcelParser

IB_HEADERS = [
    "Project",
    "Project (T)",
    "Medewerker (T)",
    "Begrote uren",
    "Prognose uren",
    "werkelijk geboekte uren",
    "Periode",
    "Resterende uren",
    "Kpl",
]
IB_ROW = ["P1", "Project", "Anne", 10, 8, 2, 1, 6, "K1"]
RHA_HEADERS = [
    "Medewerker Nummer",
    "Naam Medewerker",
    "Projectmanager",
    "Project",
    "Status Project",
    "Projectnaam",
    "Deelproject",
    "Status Deelproject",
    "Deelprojectnaam",
    "Kostenplaats",
    "Registratiedatum",
    "Uren",
    "Projecttype",
    "Parttime %",
    "Hoofdafdeling",
    "Academie",
    "Hoofdafdeling Naam",
    "Aanstellingskostenplaats",
    "Aanstellingskostenplaats",
]
RHA_ROW = [
    "42",
    "Anne",
    "Manager",
    "P1",
    "open",
    "Project",
    "S1",
    "open",
    "Subproject",
    "K1",
    date(2026, 1, 2),
    2.5,
    "Type",
    None,
    "Dept",
    "Academy",
    "Dept name",
    "ignored",
    "K2",
]


@pytest.fixture(autouse=True)
def stable_route_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "debug", False)
    monkeypatch.setattr(config, "stream_progress_interval_seconds", 0.25)


def workbook_bytes(rows: Sequence[Sequence[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(list(row))
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def parse_sse(response: Response) -> list[tuple[str, dict[str, object]]]:
    assert response.headers["content-type"].startswith("text/event-stream")
    events: list[tuple[str, dict[str, object]]] = []
    for block in response.text.strip().split("\n\n"):
        event_line, data_line = block.splitlines()
        events.append((event_line.removeprefix("event: "), json.loads(data_line[6:])))
    return events


class Writer:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.rows: list[UbwfrisRow] = []
        self.snapshot_ids: list[str] = []
        self.begun_ids: list[str] = []
        self.discarded_ids: list[str] = []
        self.fail_after: int | None = fail_after

    async def begin_snapshot(self, snapshot_id: str) -> None:
        self.begun_ids.append(snapshot_id)

    async def discard_snapshot(self, snapshot_id: str) -> None:
        self.discarded_ids.append(snapshot_id)
        self.rows.clear()

    async def write_ubwfris_rows(
        self, snapshot_id: str, rows: Sequence[UbwfrisRow]
    ) -> int:
        if self.fail_after is not None and len(self.rows) >= self.fail_after:
            raise OSError("database unavailable")
        self.rows.extend(rows)
        self.snapshot_ids.append(snapshot_id)
        return len(rows)


def app_with_writer(writer: Writer) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_ubwfris_ib_writer] = lambda: writer
    app.dependency_overrides[get_ubwfris_rha_writer] = lambda: writer
    return app


def upload(app: FastAPI, path: str, data: bytes) -> Response:
    async def request() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await asyncio.wait_for(
                client.post(path, files={"file": ("workbook.xlsx", data)}), timeout=5
            )

    return asyncio.run(request())


@pytest.mark.parametrize(
    ("path", "rows"),
    [
        ("/api/v1/ubwfris/snapshots/ib", [IB_HEADERS, IB_ROW]),
        ("/api/v1/ubwfris/snapshots/rha", [RHA_HEADERS, RHA_ROW]),
    ],
)
def test_upload_streams_progress_and_final_result(
    path: str, rows: list[list[object]]
) -> None:
    writer = Writer()
    data = workbook_bytes(rows)
    response = upload(app_with_writer(writer), path, data)

    assert response.status_code == 200
    assert parse_sse(response) == [
        (
            "progress",
            {
                "status": "progress",
                "processed_rows": 1,
                "total_rows": 1,
                "created_rows": 0,
                "invalid_rows": 0,
            },
        ),
        (
            "complete",
            {
                "status": "complete",
                "snapshot_id": sha256(data).hexdigest(),
                "processed_rows": 1,
                "total_rows": 1,
                "created_rows": 1,
                "invalid_rows": 0,
                "invalid_row_numbers": [],
            },
        ),
    ]
    assert len(writer.rows) == 1
    assert writer.snapshot_ids == [sha256(data).hexdigest()]


def test_closing_upload_discards_incomplete_snapshot() -> None:
    data = workbook_bytes([IB_HEADERS, IB_ROW])
    writer = Writer()

    async def run() -> None:
        file = UploadFile(file=BytesIO(data), filename="workbook.xlsx")
        upload_stream = _upload_ubwfris_workbook(file, UbwfrisIbExcelParser(), writer)
        _ = await anext(upload_stream)
        await upload_stream.aclose()

    asyncio.run(run())
    assert writer.begun_ids == [sha256(data).hexdigest()]
    assert writer.discarded_ids == [sha256(data).hexdigest()]
    assert writer.rows == []


def test_cancelled_upload_finishes_discard() -> None:
    data = workbook_bytes([IB_HEADERS, IB_ROW])

    class SlowDiscardWriter(Writer):
        @override
        async def discard_snapshot(self, snapshot_id: str) -> None:
            await anyio.sleep(0.01)
            await super().discard_snapshot(snapshot_id)

    writer = SlowDiscardWriter()

    async def run() -> None:
        file = UploadFile(file=BytesIO(data), filename="workbook.xlsx")
        upload_stream = _upload_ubwfris_workbook(file, UbwfrisIbExcelParser(), writer)
        async with anyio.create_task_group() as tasks:
            _ = await anext(upload_stream)
            tasks.cancel_scope.cancel()
            await upload_stream.aclose()

    asyncio.run(run())
    assert writer.discarded_ids == [sha256(data).hexdigest()]


def test_invalid_workbook_yields_error_event() -> None:
    data = b"not an xlsx"
    writer = Writer()
    response = upload(app_with_writer(writer), "/api/v1/ubwfris/snapshots/ib", data)

    assert response.status_code == 200
    assert parse_sse(response) == [
        (
            "error",
            {
                "status": "error",
                "snapshot_id": sha256(data).hexdigest(),
                "processed_rows": 0,
                "total_rows": 0,
                "created_rows": 0,
                "invalid_rows": 0,
                "invalid_row_numbers": [],
                "error_status": 422,
                "detail": "The upload is not a readable Excel workbook",
            },
        )
    ]
    assert writer.discarded_ids == [sha256(data).hexdigest()]


def test_store_failure_on_first_row_yields_error_event() -> None:
    data = workbook_bytes([IB_HEADERS, IB_ROW])
    response = upload(
        app_with_writer(Writer(fail_after=0)),
        "/api/v1/ubwfris/snapshots/ib",
        data,
    )

    assert response.status_code == 200
    assert parse_sse(response) == [
        (
            "progress",
            {
                "status": "progress",
                "processed_rows": 1,
                "total_rows": 1,
                "created_rows": 0,
                "invalid_rows": 0,
            },
        ),
        (
            "error",
            {
                "status": "error",
                "snapshot_id": sha256(data).hexdigest(),
                "processed_rows": 1,
                "total_rows": 1,
                "created_rows": 0,
                "invalid_rows": 0,
                "invalid_row_numbers": [],
                "error_status": 503,
                "detail": "Failed to save rows from the uploaded workbook",
            },
        ),
    ]


def test_debug_includes_original_exception_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "debug", True)
    data = workbook_bytes([IB_HEADERS, IB_ROW])
    response = upload(
        app_with_writer(Writer(fail_after=0)), "/api/v1/ubwfris/snapshots/ib", data
    )

    event, payload = parse_sse(response)[-1]
    assert event == "error"
    assert payload["error_status"] == 503
    assert "OSError: database unavailable" in str(payload["traceback"])
    assert "StoreUnavailable: Failed to save rows" in str(payload["traceback"])


def test_duplicate_upload_yields_conflict_event() -> None:
    class DuplicateWriter(Writer):
        @override
        async def begin_snapshot(self, snapshot_id: str) -> None:
            _ = snapshot_id
            raise DuplicateUpload("The uploaded workbook was previously imported")

    data = workbook_bytes([IB_HEADERS, IB_ROW])
    writer = DuplicateWriter()
    response = upload(app_with_writer(writer), "/api/v1/ubwfris/snapshots/ib", data)

    assert parse_sse(response) == [
        (
            "error",
            {
                "status": "error",
                "snapshot_id": sha256(data).hexdigest(),
                "processed_rows": 0,
                "total_rows": 0,
                "created_rows": 0,
                "invalid_rows": 0,
                "invalid_row_numbers": [],
                "error_status": 409,
                "detail": "The uploaded workbook was previously imported",
            },
        ),
    ]
    assert writer.discarded_ids == []


def test_completed_upload_reports_invalid_numbers_only_at_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "stream_progress_interval_seconds", 0)
    response = upload(
        app_with_writer(Writer()),
        "/api/v1/ubwfris/snapshots/ib",
        workbook_bytes(
            [IB_HEADERS, IB_ROW, ["P2", "Project", "Bob", "bad", 8, 2, 1, 6, "K1"]]
        ),
    )

    assert response.status_code == 200
    events = parse_sse(response)
    assert [name for name, _ in events] == ["progress", "progress", "complete"]
    assert all("invalid_row_numbers" not in data for _, data in events[:-1])
    assert events[-1][1]["invalid_row_numbers"] == [3]


@pytest.mark.parametrize(
    ("path", "rows"),
    [
        ("/api/v1/ubwfris/snapshots/ib", [IB_HEADERS, IB_ROW, IB_ROW]),
        ("/api/v1/ubwfris/snapshots/rha", [RHA_HEADERS, RHA_ROW, RHA_ROW]),
    ],
)
def test_upload_limits_progress_but_completes_with_final_counts(
    monkeypatch: pytest.MonkeyPatch, path: str, rows: list[list[object]]
) -> None:
    monkeypatch.setattr(config, "stream_progress_interval_seconds", 60)
    writer = Writer()
    response = upload(app_with_writer(writer), path, workbook_bytes(rows))

    assert response.status_code == 200
    events = parse_sse(response)
    assert [name for name, _ in events] == ["progress", "complete"]
    assert events[0][1]["processed_rows"] == 1
    assert events[1][1]["processed_rows"] == 2
    assert events[1][1]["created_rows"] == 2
    assert len(writer.rows) == 2


def test_all_invalid_rows_end_with_failure_event() -> None:
    data = workbook_bytes(
        [IB_HEADERS, ["P1", "Project", "Anne", "bad", 8, 2, 1, 6, "K1"]]
    )
    response = upload(
        app_with_writer(Writer()),
        "/api/v1/ubwfris/snapshots/ib",
        data,
    )

    assert response.status_code == 200
    assert parse_sse(response) == [
        (
            "progress",
            {
                "status": "progress",
                "processed_rows": 1,
                "total_rows": 1,
                "created_rows": 0,
                "invalid_rows": 1,
            },
        ),
        (
            "error",
            {
                "status": "error",
                "snapshot_id": sha256(data).hexdigest(),
                "processed_rows": 1,
                "total_rows": 1,
                "created_rows": 0,
                "invalid_rows": 1,
                "invalid_row_numbers": [2],
                "error_status": 422,
                "detail": "The uploaded workbook has no valid rows",
            },
        ),
    ]


def test_store_failure_after_progress_ends_with_error_event() -> None:
    writer = Writer(fail_after=0)
    data = workbook_bytes([IB_HEADERS, IB_ROW, IB_ROW])
    response = upload(
        app_with_writer(writer),
        "/api/v1/ubwfris/snapshots/ib",
        data,
    )

    assert response.status_code == 200
    events = parse_sse(response)
    assert [name for name, _ in events] == ["progress", "error"]
    assert events[1][1] == {
        "status": "error",
        "snapshot_id": sha256(data).hexdigest(),
        "processed_rows": 2,
        "total_rows": 2,
        "created_rows": 0,
        "invalid_rows": 0,
        "invalid_row_numbers": [],
        "error_status": 503,
        "detail": "Failed to save rows from the uploaded workbook",
    }
    assert len(writer.rows) == 0
    assert writer.discarded_ids == [sha256(data).hexdigest()]
