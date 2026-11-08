from typing import Protocol

from domain.models.employee import Employee


class EmployeeStore(Protocol):
    async def save_employee(self, employee: Employee) -> None: ...
