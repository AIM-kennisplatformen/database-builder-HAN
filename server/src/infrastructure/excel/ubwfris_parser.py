"""Read IB and RHA workbooks into typed source rows."""

from collections.abc import AsyncIterator, Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import cast, override
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel  # pyright: ignore[reportUnknownVariableType]

from domain.exceptions.ubwfris_ingest_errors import InvalidWorkbook
from domain.models.ubwfris import (
    IbRow,
    RhaRow,
    UbwfrisWorkbook,
)
from domain.ports.ubwfris.ubwfris_parser import ParsedUbwfrisRow, UbwfrisParser


class UbwfrisIbExcelParser(UbwfrisParser[IbRow]):
    @override
    def stream_ubwfris_rows(
        self, workbook: UbwfrisWorkbook[IbRow]
    ) -> AsyncIterator[ParsedUbwfrisRow[IbRow]]:
        headers = (
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

        async def rows() -> AsyncIterator[ParsedUbwfrisRow[IbRow]]:
            try:
                source = load_workbook(
                    BytesIO(workbook.file),
                    read_only=True,
                    data_only=True,
                    keep_links=False,
                )
            except (BadZipFile, KeyError, OSError, TypeError, ValueError) as exc:
                raise InvalidWorkbook(
                    "The upload is not a readable Excel workbook"
                ) from exc

            try:
                positions: dict[str, int] = {}
                for sheet in source.worksheets:
                    for header_row, values in enumerate(
                        sheet.iter_rows(values_only=True), start=1
                    ):
                        if header_row > 20:
                            break
                        positions = {
                            value.strip(): index
                            for index, value in enumerate(values)
                            if isinstance(value, str)
                        }
                        if all(header in positions for header in headers):
                            break
                    else:
                        continue
                    if header_row <= 20 and all(
                        header in positions for header in headers
                    ):
                        break
                else:
                    raise InvalidWorkbook("No worksheet has the required IB headers")

                indices = tuple(positions[header] for header in headers)

                def blank(value: object) -> bool:
                    return value is None or (
                        isinstance(value, str) and not value.strip()
                    )

                def populated(values: tuple[object, ...]) -> bool:
                    return any(
                        index < len(values) and not blank(values[index])
                        for index in indices
                    )

                total_rows = sum(
                    populated(values)
                    for values in sheet.iter_rows(
                        min_row=header_row + 1, values_only=True
                    )
                )
                for row_number, values in enumerate(
                    sheet.iter_rows(min_row=header_row + 1, values_only=True),
                    start=header_row + 1,
                ):
                    if not populated(values):
                        continue
                    try:
                        cells = [values[index] for index in indices]
                        if any(blank(value) for value in cells):
                            raise ValueError("Required IB cell is empty")
                        if any(isinstance(value, bool) for value in cells[3:8]):
                            raise ValueError("Boolean is not a numeric IB value")
                        numbers = [Decimal(str(value)) for value in cells[3:8]]
                        if any(not number.is_finite() for number in numbers):
                            raise ValueError("IB number is not finite")
                        if numbers[3] != numbers[3].to_integral_value():
                            raise ValueError("IB period is not an integer")
                        row = IbRow(
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
                    except IndexError, InvalidOperation, TypeError, ValueError:
                        yield ParsedUbwfrisRow(None, row_number, total_rows)
                        continue
                    yield ParsedUbwfrisRow(row, row_number, total_rows)
            finally:
                source.close()

        return rows()


class UbwfrisRhaExcelParser(UbwfrisParser[RhaRow]):
    @override
    def stream_ubwfris_rows(
        self, workbook: UbwfrisWorkbook[RhaRow]
    ) -> AsyncIterator[ParsedUbwfrisRow[RhaRow]]:
        headers = (
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
        excel_serial_to_date = cast(
            Callable[[int | float, datetime], object], from_excel
        )

        async def rows() -> AsyncIterator[ParsedUbwfrisRow[RhaRow]]:
            try:
                source = load_workbook(
                    BytesIO(workbook.file),
                    read_only=True,
                    data_only=True,
                    keep_links=False,
                )
            except (BadZipFile, KeyError, OSError, TypeError, ValueError) as exc:
                raise InvalidWorkbook(
                    "The upload is not a readable Excel workbook"
                ) from exc

            try:
                selected_sheet = None
                indices: tuple[int, ...] = ()
                header_row = 0
                for sheet in source.worksheets:
                    for row_number, values in enumerate(
                        sheet.iter_rows(values_only=True), start=1
                    ):
                        if row_number > 20:
                            break
                        occurrences: dict[str, list[int]] = {}
                        for index, value in enumerate(values):
                            if isinstance(value, str):
                                occurrences.setdefault(value.strip(), []).append(index)
                        positions: list[int] = []
                        for header in headers:
                            matches = occurrences.get(header, [])
                            if header == "Aanstellingskostenplaats":
                                if len(matches) >= 2:
                                    positions.append(matches[1])
                                elif fallback := occurrences.get(
                                    "Aanstellingskostenplaats,1"
                                ):
                                    positions.append(fallback[0])
                                else:
                                    break
                            elif matches:
                                positions.append(matches[0])
                            else:
                                break
                        if len(positions) == len(headers):
                            selected_sheet = sheet
                            indices = tuple(positions)
                            header_row = row_number
                            break
                    if selected_sheet is not None:
                        break
                if selected_sheet is None:
                    raise InvalidWorkbook("No worksheet has the required RHA headers")

                def blank(value: object) -> bool:
                    return value is None or (
                        isinstance(value, str) and not value.strip()
                    )

                def populated(values: tuple[object, ...]) -> bool:
                    return any(
                        index < len(values) and not blank(values[index])
                        for index in indices
                    )

                def required_text(value: object) -> str:
                    if blank(value):
                        raise ValueError("Required RHA cell is empty")
                    return str(value).strip()

                def decimal(value: object) -> Decimal:
                    if blank(value) or isinstance(value, bool):
                        raise ValueError("Invalid RHA number")
                    number = Decimal(str(value))
                    if not number.is_finite():
                        raise ValueError("RHA number is not finite")
                    return number

                def integer(value: object) -> int:
                    number = decimal(value)
                    if number != number.to_integral_value():
                        raise ValueError("RHA percentage is not an integer")
                    return int(number)

                def registration_date(value: object) -> date:
                    if isinstance(value, datetime):
                        return value.date()
                    if isinstance(value, date):
                        return value
                    if isinstance(value, int | float) and not isinstance(value, bool):
                        converted = excel_serial_to_date(value, source.epoch)
                        if isinstance(converted, datetime):
                            return converted.date()
                    raise ValueError("RHA registration date is invalid")

                total_rows = sum(
                    populated(values)
                    for values in selected_sheet.iter_rows(
                        min_row=header_row + 1, values_only=True
                    )
                )
                for row_number, values in enumerate(
                    selected_sheet.iter_rows(min_row=header_row + 1, values_only=True),
                    start=header_row + 1,
                ):
                    if not populated(values):
                        continue
                    try:
                        cells = [values[index] for index in indices]
                        row = RhaRow(
                            row_number=row_number,
                            employee_number=required_text(cells[0]),
                            employee_name=required_text(cells[1]),
                            project_manager=required_text(cells[2]),
                            project_id=required_text(cells[3]),
                            project_status=required_text(cells[4]),
                            project_name=required_text(cells[5]),
                            subproject_id=required_text(cells[6]),
                            subproject_status=required_text(cells[7]),
                            subproject_name=required_text(cells[8]),
                            cost_center=required_text(cells[9]),
                            registration_date=registration_date(cells[10]),
                            hours=decimal(cells[11]),
                            project_type=required_text(cells[12]),
                            parttime_percentage=(
                                None if blank(cells[13]) else integer(cells[13])
                            ),
                            main_department=required_text(cells[14]),
                            academy=required_text(cells[15]),
                            main_department_name=required_text(cells[16]),
                            employment_cost_center=required_text(cells[17]),
                        )
                    except IndexError, InvalidOperation, TypeError, ValueError:
                        yield ParsedUbwfrisRow(None, row_number, total_rows)
                        continue
                    yield ParsedUbwfrisRow(row, row_number, total_rows)
            finally:
                source.close()

        return rows()
