import asyncio
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook

from domain.exceptions.ubwfris_ingest_errors import InvalidWorkbook
from domain.models.ubwfris import IbRow, RhaRow, UbwfrisWorkbook
from infrastructure.sources.ubwfris_excel_parser import (
    UbwfrisIbExcelParser,
    UbwfrisRhaExcelParser,
)

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
    "Aanstellingskostenplaats,1",
]


def workbook_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def parse_ib(data: bytes) -> list[tuple[IbRow | None, int, int]]:
    async def collect() -> list[tuple[IbRow | None, int, int]]:
        return [
            item
            async for item in UbwfrisIbExcelParser().stream_ubwfris_rows(
                UbwfrisWorkbook(data)
            )
        ]

    return asyncio.run(collect())


def parse_rha(data: bytes) -> list[tuple[RhaRow | None, int, int]]:
    async def collect() -> list[tuple[RhaRow | None, int, int]]:
        return [
            item
            async for item in UbwfrisRhaExcelParser().stream_ubwfris_rows(
                UbwfrisWorkbook(data)
            )
        ]

    return asyncio.run(collect())


def test_ib_parser_reports_malformed_rows_and_preserves_row_numbers() -> None:
    data = workbook_bytes(
        IB_HEADERS,
        [
            ["P1", "Project", "Anne", 10, 8, 2, 1, 6, "K1"],
            ["P2", "Project", "Bob", "not a number", 8, 2, 1, 6, "K1"],
        ],
    )

    results = parse_ib(data)

    assert [row_number for _, row_number, _ in results] == [2, 3]
    assert [total_rows for _, _, total_rows in results] == [2, 2]
    assert results[0][0] == IbRow(
        2,
        "P1",
        "Project",
        "Anne",
        Decimal(10),
        Decimal(8),
        Decimal(2),
        1,
        Decimal(6),
        "K1",
    )
    assert results[1][0] is None


def test_rha_parser_converts_date_and_optional_percentage() -> None:
    data = workbook_bytes(
        RHA_HEADERS,
        [
            [
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
                "K2",
            ]
        ],
    )

    results = parse_rha(data)

    assert len(results) == 1
    assert isinstance(results[0][0], RhaRow)
    row = results[0][0]
    assert isinstance(row, RhaRow)
    assert row.registration_date == date(2026, 1, 2)
    assert row.parttime_percentage is None


@pytest.mark.parametrize(
    "data", [b"not an xlsx", workbook_bytes(["Other"], [["value"]])]
)
def test_unreadable_or_wrong_format_workbook_is_invalid(data: bytes) -> None:
    with pytest.raises(InvalidWorkbook):
        parse_ib(data)
