"""Read IB and RHA workbooks into typed source rows."""

from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Protocol, cast, override
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel  # pyright: ignore[reportUnknownVariableType]
from openpyxl.workbook import Workbook

from domain.exceptions.ubwfris_ingest_errors import InvalidWorkbook
from domain.models.ubwfris import IbRow, RhaRow, UbwfrisWorkbook
from domain.ports.ubwfris.ubwfris_parser import ParsedUbwfrisRow, UbwfrisParser

_IB_HEADERS = (
    "Project",
    "Project (T)",
    "Medewerker (T)",
    "Begrote uren",
    "Prognose uren",
    "werkelijk geboekte uren",
    "Periode",
    "Resterende uren",
    "Kpl",
)
_RHA_HEADERS = (
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
)
_EXCEL_SERIAL_TO_DATE = cast(Callable[[int | float, datetime], object], from_excel)


class _Worksheet(Protocol):
    def iter_rows(
        self, min_row: int = 1, *, values_only: bool = False
    ) -> Iterator[tuple[object, ...]]: ...


class UbwfrisIbExcelParser(UbwfrisParser[IbRow]):
    @override
    async def stream_ubwfris_rows(
        self, workbook: UbwfrisWorkbook[IbRow]
    ) -> AsyncIterator[ParsedUbwfrisRow[IbRow]]:
        source = _open_workbook(workbook)
        try:
            sheet, header_row, indices = _find_ib_sheet(source)
            total_rows = sum(
                _has_populated_values(values, indices)
                for values in sheet.iter_rows(min_row=header_row + 1, values_only=True)
            )
            for row_number, values in enumerate(
                sheet.iter_rows(min_row=header_row + 1, values_only=True),
                start=header_row + 1,
            ):
                if not _has_populated_values(values, indices):
                    continue
                try:
                    row = _parse_ib_row(values, indices, row_number)
                except IndexError, InvalidOperation, TypeError, ValueError:
                    yield ParsedUbwfrisRow(None, row_number, total_rows)
                    continue
                yield ParsedUbwfrisRow(row, row_number, total_rows)
        finally:
            source.close()


class UbwfrisRhaExcelParser(UbwfrisParser[RhaRow]):
    @override
    async def stream_ubwfris_rows(
        self, workbook: UbwfrisWorkbook[RhaRow]
    ) -> AsyncIterator[ParsedUbwfrisRow[RhaRow]]:
        source = _open_workbook(workbook)
        try:
            sheet, header_row, indices = _find_rha_sheet(source)
            total_rows = sum(
                _has_populated_values(values, indices)
                for values in sheet.iter_rows(min_row=header_row + 1, values_only=True)
            )
            for row_number, values in enumerate(
                sheet.iter_rows(min_row=header_row + 1, values_only=True),
                start=header_row + 1,
            ):
                if not _has_populated_values(values, indices):
                    continue
                try:
                    row = _parse_rha_row(values, indices, row_number, source.epoch)
                except IndexError, InvalidOperation, TypeError, ValueError:
                    yield ParsedUbwfrisRow(None, row_number, total_rows)
                    continue
                yield ParsedUbwfrisRow(row, row_number, total_rows)
        finally:
            source.close()


def _open_workbook(
    workbook: UbwfrisWorkbook[IbRow] | UbwfrisWorkbook[RhaRow],
) -> Workbook:
    try:
        return load_workbook(
            BytesIO(workbook.file),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
    except (BadZipFile, KeyError, OSError, TypeError, ValueError) as exc:
        raise InvalidWorkbook("The upload is not a readable Excel workbook") from exc


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _has_populated_values(values: tuple[object, ...], indices: tuple[int, ...]) -> bool:
    return any(
        index < len(values) and not _is_blank(values[index]) for index in indices
    )


def _build_header_positions(values: tuple[object, ...]) -> dict[str, int]:
    return {
        value.strip(): index
        for index, value in enumerate(values)
        if isinstance(value, str)
    }


def _find_ib_sheet(
    source: Workbook,
) -> tuple[_Worksheet, int, tuple[int, ...]]:
    for sheet in source.worksheets:
        for header_row, values in enumerate(sheet.iter_rows(values_only=True), start=1):
            if header_row > 20:
                break
            positions = _build_header_positions(values)
            if all(header in positions for header in _IB_HEADERS):
                return (
                    cast(_Worksheet, cast(object, sheet)),
                    header_row,
                    tuple(positions[header] for header in _IB_HEADERS),
                )
    raise InvalidWorkbook("No worksheet has the required IB headers")


def _parse_ib_row(
    values: tuple[object, ...], indices: tuple[int, ...], row_number: int
) -> IbRow:
    cells = [values[index] for index in indices]
    if any(_is_blank(value) for value in cells):
        raise ValueError("Required IB cell is empty")
    if any(isinstance(value, bool) for value in cells[3:8]):
        raise ValueError("Boolean is not a numeric IB value")
    numbers = [Decimal(str(value)) for value in cells[3:8]]
    if any(not number.is_finite() for number in numbers):
        raise ValueError("IB number is not finite")
    if numbers[3] != numbers[3].to_integral_value():
        raise ValueError("IB period is not an integer")
    return IbRow(
        row_number=row_number,
        project_id=str(cells[0]).strip(),
        project_name=str(cells[1]).strip(),
        employee_name=str(cells[2]).strip(),
        budgeted_hours=numbers[0],
        projected_hours=numbers[1],
        booked_hours=numbers[2],
        period=int(numbers[3]),
        remaining_hours=numbers[4],
        cost_center=str(cells[8]).strip(),
    )


def _find_rha_header_indices(values: tuple[object, ...]) -> tuple[int, ...] | None:
    occurrences: dict[str, list[int]] = {}
    for index, value in enumerate(values):
        if isinstance(value, str):
            occurrences.setdefault(value.strip(), []).append(index)
    positions: list[int] = []
    for header in _RHA_HEADERS:
        matches = occurrences.get(header, [])
        if header == "Aanstellingskostenplaats":
            if len(matches) >= 2:
                positions.append(matches[1])
            elif fallback := occurrences.get("Aanstellingskostenplaats,1"):
                positions.append(fallback[0])
            else:
                return None
        elif matches:
            positions.append(matches[0])
        else:
            return None
    return tuple(positions)


def _find_rha_sheet(source: Workbook) -> tuple[_Worksheet, int, tuple[int, ...]]:
    for sheet in source.worksheets:
        for row_number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
            if row_number > 20:
                break
            indices = _find_rha_header_indices(values)
            if indices is not None:
                return cast(_Worksheet, cast(object, sheet)), row_number, indices
    raise InvalidWorkbook("No worksheet has the required RHA headers")


def _parse_required_text(value: object) -> str:
    if _is_blank(value):
        raise ValueError("Required RHA cell is empty")
    return str(value).strip()


def _parse_decimal(value: object) -> Decimal:
    if _is_blank(value) or isinstance(value, bool):
        raise ValueError("Invalid RHA number")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("RHA number is not finite")
    return number


def _parse_integer(value: object) -> int:
    number = _parse_decimal(value)
    if number != number.to_integral_value():
        raise ValueError("RHA percentage is not an integer")
    return int(number)


def _parse_registration_date(value: object, epoch: datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        converted = _EXCEL_SERIAL_TO_DATE(value, epoch)
        if isinstance(converted, datetime):
            return converted.date()
    raise ValueError("RHA registration date is invalid")


def _parse_rha_row(
    values: tuple[object, ...],
    indices: tuple[int, ...],
    row_number: int,
    epoch: datetime,
) -> RhaRow:
    cells = [values[index] for index in indices]
    return RhaRow(
        row_number=row_number,
        employee_number=_parse_required_text(cells[0]),
        employee_name=_parse_required_text(cells[1]),
        project_manager=_parse_required_text(cells[2]),
        project_id=_parse_required_text(cells[3]),
        project_status=_parse_required_text(cells[4]),
        project_name=_parse_required_text(cells[5]),
        subproject_id=_parse_required_text(cells[6]),
        subproject_status=_parse_required_text(cells[7]),
        subproject_name=_parse_required_text(cells[8]),
        cost_center=_parse_required_text(cells[9]),
        registration_date=_parse_registration_date(cells[10], epoch),
        hours=_parse_decimal(cells[11]),
        project_type=_parse_required_text(cells[12]),
        parttime_percentage=None if _is_blank(cells[13]) else _parse_integer(cells[13]),
        main_department=_parse_required_text(cells[14]),
        academy=_parse_required_text(cells[15]),
        main_department_name=_parse_required_text(cells[16]),
        employment_cost_center=_parse_required_text(cells[17]),
    )
