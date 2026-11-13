import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from domain.models.ubwfris import (
    RhaRow,
    UbwfrisIngestEvent,
    UbwfrisIngestProgress,
    UbwfrisIngestRequest,
    UbwfrisIngestResult,
    UbwfrisIngestStatus,
    UbwfrisRow,
    UbwfrisWorkbook,
)
from domain.use_cases.ubwfris_ingest import (
    _validate_rha_row,  # pyright: ignore[reportPrivateUsage]
    ingest_ubwfris_workbook,
)


def rha_row() -> RhaRow:
    return RhaRow(
        row_number=2,
        employee_number="42",
        employee_name="Anne",
        project_manager="Manager",
        project_id="P1",
        project_status="open",
        project_name="Project",
        subproject_id="S1",
        subproject_status="open",
        subproject_name="Subproject",
        cost_center="K1",
        registration_date=date(2026, 1, 2),
        hours=Decimal("2.5"),
        project_type="Type",
        parttime_percentage=None,
        main_department="Dept",
        academy="Academy",
        main_department_name="Dept name",
        employment_cost_center="K2",
    )


class SnapshotWriter:
    async def begin_snapshot(self, snapshot_id: str) -> None:
        _ = snapshot_id

    async def discard_snapshot(self, snapshot_id: str) -> None:
        _ = snapshot_id


@pytest.mark.parametrize(
    "field",
    [
        "employee_number",
        "employee_name",
        "project_manager",
        "project_id",
        "project_status",
        "project_name",
        "subproject_id",
        "subproject_status",
        "subproject_name",
        "cost_center",
        "project_type",
        "main_department",
        "academy",
        "main_department_name",
        "employment_cost_center",
    ],
)
def test_rha_validation_rejects_blank_required_text(field: str) -> None:
    _, is_valid = _validate_rha_row(replace(rha_row(), **{field: "  "}))
    assert not is_valid


@pytest.mark.parametrize(
    "changes",
    [
        {"row_number": 0},
        {"hours": Decimal("NaN")},
        {"hours": Decimal("Infinity")},
        {"registration_date": "2026-01-02"},
        {"parttime_percentage": True},
    ],
)
def test_rha_validation_rejects_invalid_source_facts(
    changes: dict[str, object],
) -> None:
    _, is_valid = _validate_rha_row(replace(rha_row(), **changes))
    assert not is_valid


def test_rha_validation_normalizes_text_and_accepts_optional_percentage() -> None:
    row = replace(
        rha_row(),
        employee_name=" Anne  van Dijk ",
        project_id=" P1 ",
        employment_cost_center=" K2 ",
        parttime_percentage=50,
        hours=Decimal("-2.5"),
    )

    normalized, is_valid = _validate_rha_row(row)

    assert is_valid
    assert normalized == replace(
        rha_row(),
        employee_name="Anne  van Dijk",
        parttime_percentage=50,
        hours=Decimal("-2.5"),
    )


def test_rha_ingest_writes_normalized_valid_row_only() -> None:
    valid = replace(rha_row(), employee_name=" Anne ", project_id=" P1 ")
    invalid = replace(rha_row(), row_number=3, project_id=" ")

    class Parser:
        async def stream_ubwfris_rows(self, workbook: UbwfrisWorkbook[RhaRow]):
            _ = workbook
            yield valid, 2, 2
            yield invalid, 3, 2

    class Writer(SnapshotWriter):
        def __init__(self) -> None:
            self.rows: list[UbwfrisRow] = []

        async def write_ubwfris_rows(
            self, snapshot_id: str, rows: Sequence[RhaRow]
        ) -> int:
            assert snapshot_id == "snapshot-id"
            self.rows.extend(rows)
            return len(rows)

    writer = Writer()

    async def run() -> list[UbwfrisIngestEvent]:
        return [
            event
            async for event in ingest_ubwfris_workbook(
                UbwfrisIngestRequest(UbwfrisWorkbook[RhaRow](b"workbook")),
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
    assert writer.rows == [rha_row()]
