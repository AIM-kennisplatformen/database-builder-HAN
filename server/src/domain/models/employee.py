from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SyncEmployeesRequest:
    limit: int | None = None


@dataclass
class SyncEmployeesProgress:
    processed: int
    total: int
    available: int


@dataclass
class Employee:
    id: str
    display_name: str | None = None
    given_name: str | None = None
    surname: str | None = None
    mail: str | None = None
    employment_details: EmploymentDetails | None = None
    self_reported_details: EmployeeSelfReportedDetails | None = None


@dataclass
class EmploymentDetails:
    company_name: str | None = None
    department: str | None = None
    job_title: str | None = None


@dataclass
class EmployeeSelfReportedDetails:
    skills: list[str] | None = None
    responsibilities: list[str] | None = None
    interests: list[str] | None = None
