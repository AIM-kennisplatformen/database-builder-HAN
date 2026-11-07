"""User API response schemas."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)

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


class RefreshUsersResponse(BaseModel):
    saved: int
