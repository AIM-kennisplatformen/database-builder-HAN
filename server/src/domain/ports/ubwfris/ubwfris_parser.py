"""Parse UBWFRIS workbook rows for ingestion."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from domain.models.ubwfris import UbwfrisRow, UbwfrisWorkbook


@dataclass(frozen=True)
class ParsedUbwfrisRow[T: UbwfrisRow]:
    row: T | None
    row_number: int
    total_rows: int


class UbwfrisParser[T: UbwfrisRow](Protocol):
    def stream_ubwfris_rows(
        self, workbook: UbwfrisWorkbook[T]
    ) -> AsyncIterator[ParsedUbwfrisRow[T]]: ...
