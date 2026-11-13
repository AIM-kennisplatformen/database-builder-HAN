import asyncio
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from typing import override

import pytest

from domain.exceptions.ubwfris_ingest_errors import InvalidWorkbook, StoreUnavailable
from domain.models.ubwfris import (
    IbRow,
    UbwfrisIngestEvent,
    UbwfrisIngestProgress,
    UbwfrisIngestRequest,
    UbwfrisIngestResult,
    UbwfrisIngestStatus,
    UbwfrisRow,
    UbwfrisWorkbook,
)
from domain.use_cases.ubwfris_ingest import (
    _validate_ib_row,  # pyright: ignore[reportPrivateUsage]
    ingest_ubwfris_workbook,
)


def ib_row() -> IbRow:
    return IbRow(
        row_number=2,
        project_id="P1",
        project_name="Project",
        employee_name="Anne",
        budgeted_hours=Decimal(10),
        projected_hours=Decimal(8),
        booked_hours=Decimal(2),
        period=1,
        remaining_hours=Decimal(6),
        cost_center="K1",
    )


class SnapshotWriter:
    async def begin_snapshot(self, snapshot_id: str) -> None:
        _ = snapshot_id

    async def discard_snapshot(self, snapshot_id: str) -> None:
        _ = snapshot_id


@pytest.mark.parametrize(
    "changes",
    [
        {"row_number": 0},
        {"project_id": "  "},
        {"project_name": ""},
        {"employee_name": "\t"},
        {"cost_center": ""},
        {"budgeted_hours": Decimal("NaN")},
        {"projected_hours": Decimal("Infinity")},
        {"booked_hours": Decimal("-Infinity")},
        {"remaining_hours": Decimal("NaN")},
    ],
)
def test_ib_validation_rejects_missing_or_nonfinite_source_facts(
    changes: dict[str, object],
) -> None:
    normalized, is_valid = _validate_ib_row(replace(ib_row(), **changes))
    assert not is_valid
    assert normalized.row_number == changes.get("row_number", 2)


def test_ib_validation_accepts_finite_hour_adjustments() -> None:
    row = replace(ib_row(), booked_hours=Decimal(-2))
    assert _validate_ib_row(row) == (row, True)


def test_ib_validation_trims_text_without_changing_internal_spacing() -> None:
    row = replace(
        ib_row(),
        project_id=" P1 ",
        project_name=" Project ",
        employee_name=" Anne  van Dijk ",
        cost_center=" K1 ",
    )

    normalized, is_valid = _validate_ib_row(row)

    assert is_valid
    assert normalized == replace(ib_row(), employee_name="Anne  van Dijk")


def test_ib_ingest_only_writes_valid_rows() -> None:
    valid = replace(ib_row(), project_id=" P1 ", employee_name=" Anne ")
    invalid = replace(valid, row_number=3, project_id=" ")

    class Parser:
        async def stream_ubwfris_rows(self, workbook: UbwfrisWorkbook[IbRow]):
            _ = workbook
            yield valid, 2, 2
            yield invalid, 3, 2

    class Writer(SnapshotWriter):
        def __init__(self) -> None:
            self.rows: list[UbwfrisRow] = []

        async def write_ubwfris_rows(
            self, snapshot_id: str, rows: Sequence[IbRow]
        ) -> int:
            assert snapshot_id == "snapshot-id"
            self.rows.extend(rows)
            return len(rows)

    writer = Writer()

    async def run() -> list[UbwfrisIngestEvent]:
        return [
            event
            async for event in ingest_ubwfris_workbook(
                UbwfrisIngestRequest(UbwfrisWorkbook[IbRow](b"workbook")),
                Parser(),
                writer,
                "snapshot-id",
            )
        ]

    assert asyncio.run(run()) == [
        UbwfrisIngestProgress(
            total_rows=2, processed_rows=1, created_rows=0, invalid_rows=0
        ),
        UbwfrisIngestProgress(
            total_rows=2, processed_rows=2, created_rows=0, invalid_rows=1
        ),
        UbwfrisIngestResult(
            status=UbwfrisIngestStatus.COMPLETED,
            total_rows=2,
            processed_rows=2,
            created_rows=1,
            invalid_rows=1,
            invalid_row_numbers=(3,),
        ),
    ]
    assert writer.rows == [ib_row()]


def test_ib_ingest_batches_writes_and_counts_only_committed_rows() -> None:
    class Parser:
        async def stream_ubwfris_rows(self, workbook: UbwfrisWorkbook[IbRow]):
            _ = workbook
            for index in range(1001):
                yield replace(ib_row(), row_number=index + 2), index + 2, 1001

    class Writer(SnapshotWriter):
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        async def write_ubwfris_rows(
            self, snapshot_id: str, rows: Sequence[IbRow]
        ) -> int:
            assert snapshot_id == "snapshot-id"
            self.batch_sizes.append(len(rows))
            return len(rows)

    writer = Writer()

    async def run() -> list[UbwfrisIngestEvent]:
        return [
            event
            async for event in ingest_ubwfris_workbook(
                UbwfrisIngestRequest(UbwfrisWorkbook[IbRow](b"workbook")),
                Parser(),
                writer,
                "snapshot-id",
            )
        ]

    events = asyncio.run(run())
    assert writer.batch_sizes == [1000, 1]
    assert isinstance(events[998], UbwfrisIngestProgress)
    assert events[998].created_rows == 0
    assert isinstance(events[999], UbwfrisIngestProgress)
    assert events[999].created_rows == 1000
    assert isinstance(events[-1], UbwfrisIngestResult)
    assert events[-1].created_rows == 1001


def test_ib_ingest_fails_when_all_rows_are_invalid() -> None:
    class Parser:
        async def stream_ubwfris_rows(self, workbook: UbwfrisWorkbook[IbRow]):
            _ = workbook
            yield None, 2, 2
            yield replace(ib_row(), row_number=3, project_id=" "), 3, 2

    class Writer(SnapshotWriter):
        async def write_ubwfris_rows(
            self, snapshot_id: str, rows: Sequence[IbRow]
        ) -> int:
            _ = snapshot_id
            pytest.fail(f"Unexpected write: {rows}")

    events: list[UbwfrisIngestEvent] = []

    async def run() -> None:
        async for event in ingest_ubwfris_workbook(
            UbwfrisIngestRequest(UbwfrisWorkbook[IbRow](b"workbook")),
            Parser(),
            Writer(),
            "snapshot-id",
        ):
            events.append(event)

    with pytest.raises(InvalidWorkbook) as failure:
        asyncio.run(run())

    assert events == [
        UbwfrisIngestProgress(
            total_rows=2, processed_rows=1, created_rows=0, invalid_rows=1
        ),
        UbwfrisIngestProgress(
            total_rows=2, processed_rows=2, created_rows=0, invalid_rows=2
        ),
    ]
    assert str(failure.value) == "The uploaded workbook has no valid rows"
    assert failure.value.invalid_rows == 2
    assert failure.value.invalid_row_numbers == (2, 3)


def test_ib_ingest_raises_for_empty_upload() -> None:
    class Parser:
        async def stream_ubwfris_rows(self, workbook: UbwfrisWorkbook[IbRow]):
            _ = workbook
            yield None, 0, 0

    class Writer(SnapshotWriter):
        async def write_ubwfris_rows(
            self, snapshot_id: str, rows: Sequence[IbRow]
        ) -> int:
            _ = snapshot_id
            pytest.fail(f"Unexpected write: {rows}")

    async def run() -> list[UbwfrisIngestEvent]:
        return [
            event
            async for event in ingest_ubwfris_workbook(
                UbwfrisIngestRequest(UbwfrisWorkbook[IbRow](b"")),
                Parser(),
                Writer(),
                "snapshot-id",
            )
        ]

    with pytest.raises(InvalidWorkbook) as failure:
        asyncio.run(run())
    assert str(failure.value) == "The uploaded workbook is empty"
    assert failure.value.invalid_row_numbers == ()


def test_ib_ingest_reports_partial_write_on_failure() -> None:
    class Parser:
        async def stream_ubwfris_rows(self, workbook: UbwfrisWorkbook[IbRow]):
            _ = workbook
            for index in range(1001):
                yield replace(ib_row(), row_number=index + 2), index + 2, 1001

    class Writer(SnapshotWriter):
        def __init__(self) -> None:
            self.rows: list[IbRow] = []
            self.discarded: bool = False

        @override
        async def discard_snapshot(self, snapshot_id: str) -> None:
            assert snapshot_id == "snapshot-id"
            self.rows.clear()
            self.discarded = True

        async def write_ubwfris_rows(
            self, snapshot_id: str, rows: Sequence[IbRow]
        ) -> int:
            assert snapshot_id == "snapshot-id"
            if self.rows:
                raise OSError("database unavailable")
            self.rows.extend(rows)
            return len(rows)

    writer = Writer()

    events: list[UbwfrisIngestEvent] = []

    async def run() -> None:
        async for event in ingest_ubwfris_workbook(
            UbwfrisIngestRequest(UbwfrisWorkbook[IbRow](b"workbook")),
            Parser(),
            writer,
            "snapshot-id",
        ):
            events.append(event)

    with pytest.raises(StoreUnavailable, match="Failed to save rows"):
        asyncio.run(run())
    assert len(events) == 1001
    assert isinstance(events[999], UbwfrisIngestProgress)
    assert events[999].created_rows == 1000
    assert writer.rows == []
    assert writer.discarded
