import asyncio
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook

from domain.exceptions.ubwfris_ingest_errors import InvalidWorkbook
from domain.models.ubwfris import RhaRow, UbwfrisWorkbook
from infrastructure.sources.ubwfris_excel_parser import UbwfrisRhaExcelParser

HEADERS = [
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


def workbook_bytes(rows: Sequence[Sequence[str | int | float | date | None]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(list(row))
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def parse(data: bytes) -> list[tuple[RhaRow | None, int, int]]:
    async def collect() -> list[tuple[RhaRow | None, int, int]]:
        return [
            item
            async for item in UbwfrisRhaExcelParser().stream_ubwfris_rows(
                UbwfrisWorkbook(data)
            )
        ]

    return asyncio.run(collect())


def test_rha_parser_uses_second_cost_center_and_reports_invalid_rows() -> None:
    results = parse(
        workbook_bytes(
            [
                HEADERS,
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
                    "ignored",
                    "K2",
                ],
                [None] * 19,
                [
                    "43",
                    "Bob",
                    "Manager",
                    "P1",
                    "open",
                    "Project",
                    "S1",
                    "open",
                    "Subproject",
                    "K1",
                    "not a date",
                    2.5,
                    "Type",
                    50,
                    "Dept",
                    "Academy",
                    "Dept name",
                    "ignored",
                    "K2",
                ],
            ]
        )
    )

    assert results == [
        (
            RhaRow(
                2,
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
                Decimal("2.5"),
                "Type",
                None,
                "Dept",
                "Academy",
                "Dept name",
                "K2",
            ),
            2,
            2,
        ),
        (None, 4, 2),
    ]


def test_rha_parser_accepts_fallback_cost_center_header() -> None:
    headers = HEADERS[:-1]
    headers[-1] = "Aanstellingskostenplaats,1"
    values: list[str | int | float | date | None] = [
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
        46024,
        2.5,
        "Type",
        50,
        "Dept",
        "Academy",
        "Dept name",
        "K2",
    ]

    results = parse(workbook_bytes([headers, values]))

    assert len(results) == 1
    assert results[0][0] is not None
    assert results[0][0].registration_date == date(2026, 1, 2)
    assert results[0][0].employment_cost_center == "K2"
    assert results[0][0].parttime_percentage == 50


@pytest.mark.parametrize("data", [b"not an xlsx", workbook_bytes([["Other"]])])
def test_rha_parser_rejects_unreadable_or_wrong_format_workbooks(data: bytes) -> None:
    with pytest.raises(InvalidWorkbook):
        parse(data)
