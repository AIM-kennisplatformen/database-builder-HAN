from typing import Protocol

from fastapi.requests import Request

from domain.ports.persistence.employee_store import EmployeeStore
from domain.ports.sources.employee_source import EmployeeSource


class ApplicationState(Protocol):
    employee_source: EmployeeSource
    employee_store: EmployeeStore


def _state(request: Request) -> ApplicationState:
    return request.app.state  # pyright: ignore[reportAny]


def get_employee_source(request: Request) -> EmployeeSource:
    return _state(request).employee_source


def get_employee_store(request: Request) -> EmployeeStore:
    return _state(request).employee_store
