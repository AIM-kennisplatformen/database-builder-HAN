from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Employee:
    """Represent an employee profile with optional employment and self-reported details."""

    id: str
    display_name: str | None = None
    given_name: str | None = None
    surname: str | None = None
    mail: str | None = None
    employment_details: EmploymentDetails | None = None
    self_reported_details: EmployeeSelfReportedDetails | None = None


@dataclass(frozen=True)
class EmploymentDetails:
    """Represent organization supplied company, department, and role details."""

    company_name: str | None = None
    department: str | None = None
    job_title: str | None = None


@dataclass(frozen=True)
class EmployeeSelfReportedDetails:
    """Represent skills, responsibilities, and interests supplied by an employee."""

    skills: list[str] | None = None
    responsibilities: list[str] | None = None
    interests: list[str] | None = None
