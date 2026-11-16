from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Employee:
    id: str
    display_name: str | None = None
    given_name: str | None = None
    surname: str | None = None
    mail: str | None = None
    employment_details: EmploymentDetails | None = None
    self_reported_details: EmployeeSelfReportedDetails | None = None


@dataclass(frozen=True)
class EmploymentDetails:
    company_name: str | None = None
    department: str | None = None
    job_title: str | None = None


@dataclass(frozen=True)
class EmployeeSelfReportedDetails:
    skills: list[str] | None = None
    responsibilities: list[str] | None = None
    interests: list[str] | None = None
