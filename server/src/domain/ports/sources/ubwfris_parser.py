"""Parse UBWFRIS workbook rows for ingestion."""

from collections.abc import AsyncIterator
from typing import Protocol

from domain.models.ubwfris import UbwfrisRow, UbwfrisWorkbook


class UbwfrisParser[T: UbwfrisRow](Protocol):
    def stream_ubwfris_rows(
        self, workbook: UbwfrisWorkbook[T]
    ) -> AsyncIterator[tuple[T | None, int, int]]: ...
