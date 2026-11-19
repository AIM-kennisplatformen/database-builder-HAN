"""The metadata filters that identify an operator's catalog view."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MetadataSelection:
    root_item_id: str
    extensions: tuple[str, ...]
    include_paths: tuple[str, ...]
    exclude_paths: tuple[str, ...]
    exclude_extensions: tuple[str, ...]
