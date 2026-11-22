"""Failures that callers of a UBWFRIS ingest can handle."""

from domain.exceptions.error import DomainError


class InvalidWorkbook(DomainError):
    """The upload cannot be interpreted as the specified UBWFRIS format."""

    invalid_row_count: int
    invalid_row_numbers: tuple[int, ...]

    def __init__(
        self, message: str, *, invalid_row_numbers: tuple[int, ...] = ()
    ) -> None:
        super().__init__(message)
        self.invalid_row_numbers = invalid_row_numbers
        self.invalid_row_count = len(invalid_row_numbers)


class DuplicateUpload(DomainError):
    """The uploaded bytes were previously imported for this format."""


class UbwfrisStoreUnavailable(DomainError):
    """The UBWFRIS store cannot complete an ingest."""


class ParserFailure(DomainError):
    """The ubwfris parser failed to parse to the workbook"""
