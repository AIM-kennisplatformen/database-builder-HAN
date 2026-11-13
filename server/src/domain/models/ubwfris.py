"""UBWFRIS workbook rows and ingest progress."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


@dataclass(frozen=True)
class UbwfrisWorkbook[T: UbwfrisRow]:
    file: bytes


@dataclass(frozen=True)
class UbwfrisIngestRequest[T: UbwfrisRow]:
    workbook: UbwfrisWorkbook[T]


@dataclass(frozen=True)
class UbwfrisIngestProgress:
    total_rows: int
    processed_rows: int
    created_rows: int
    invalid_rows: int


class UbwfrisIngestStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class UbwfrisIngestResult:
    status: UbwfrisIngestStatus
    total_rows: int
    processed_rows: int
    created_rows: int
    invalid_rows: int
    invalid_row_numbers: tuple[int, ...]
    error: str | None = None


type UbwfrisIngestEvent = UbwfrisIngestProgress | UbwfrisIngestResult


type UbwfrisRow = IbRow | RhaRow


@dataclass(frozen=True)
class IbRow:
    row_number: int
    project_id: str
    project_name: str
    employee_name: str
    budgeted_hours: Decimal
    projected_hours: Decimal
    booked_hours: Decimal
    period: int
    remaining_hours: Decimal
    cost_center: str


@dataclass(frozen=True)
class RhaRow:
    row_number: int
    employee_number: str
    employee_name: str
    project_manager: str
    project_id: str
    project_status: str
    project_name: str
    subproject_id: str
    subproject_status: str
    subproject_name: str
    cost_center: str
    registration_date: date
    hours: Decimal
    project_type: str
    parttime_percentage: int | None
    main_department: str
    academy: str
    main_department_name: str
    employment_cost_center: str
