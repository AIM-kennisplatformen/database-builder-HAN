from dataclasses import dataclass

from domain.models.base import ValueObject


@dataclass(frozen=True)
class ContentVersion(ValueObject):
    version: str | None
