import asyncio
from collections.abc import Sequence
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook

from domain.exceptions.ubwfris_ingest_errors import InvalidWorkbook
from domain.models.ubwfris import IbRow, UbwfrisWorkbook
from infrastructure.sources.ubwfris_excel_parser import UbwfrisIbExcelParser

HEADERS = [
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


def workbook_bytes(rows: Sequence[Sequence[str | int | None]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(list(row))
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def parse(data: bytes) -> list[tuple[IbRow | None, int, int]]:
    async def collect() -> list[tuple[IbRow | None, int, int]]:
        return [
            item
            async for item in UbwfrisIbExcelParser().stream_ubwfris_rows(
                UbwfrisWorkbook(data)
            )
        ]

    return asyncio.run(collect())


def test_ib_parser_keeps_row_numbers_and_reports_invalid_rows() -> None:
    results = parse(
        workbook_bytes(
            [
                ["Exported on 2026-01-01"],
                HEADERS,
                ["P1", "Project", "Anne", 10, 8, 2, 1, 6, "K1"],
                [None] * 9,
                ["P2", "Project", "Bob", "not a number", 8, 2, 1, 6, "K1"],
            ]
        )
    )

    assert results == [
        (
            IbRow(
                3,
                "P1",
                "Project",
                "Anne",
                Decimal(10),
                Decimal(8),
                Decimal(2),
                1,
                Decimal(6),
                "K1",
            ),
            3,
            2,
        ),
        (None, 5, 2),
    ]


@pytest.mark.parametrize(
    "data", [b"not an xlsx", workbook_bytes([["Other"], ["value"]])]
)
def test_ib_parser_rejects_unreadable_or_wrong_format_workbooks(data: bytes) -> None:
    with pytest.raises(InvalidWorkbook):
        parse(data)
