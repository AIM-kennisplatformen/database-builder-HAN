"""Failures that callers of the employee sync use case can handle."""

from domain.exceptions.error import DomainError


class InvalidLimit(DomainError):
    """A sync limit must be a positive number of employees."""


class EmployeeSourceUnavailable(DomainError):
    """The configured employee source cannot provide a snapshot."""


class EmployeeStoreUnavailable(DomainError):
    """The employee store could not save the fetched employees."""


class NoEmployeesFound(DomainError):
    """The employee source returned no employees to sync."""
