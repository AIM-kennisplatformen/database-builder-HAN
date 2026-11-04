"""Source-independent user data."""

from dataclasses import dataclass


@dataclass
class User:
    id: str
    display_name: str | None = None
    given_name: str | None = None
    surname: str | None = None
    mail: str | None = None
    company_name: str | None = None
    department: str | None = None
    job_title: str | None = None
    skills: list[str] | None = None
    responsibilities: list[str] | None = None
    interests: list[str] | None = None
