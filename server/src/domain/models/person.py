from dataclasses import dataclass
from typing import override

from domain.models.base import ID, AggregateRoot


@dataclass(frozen=True)
class PersonId(ID[str]):
    _id: str

    def __post_init__(self) -> None:
        if not self._id.strip():
            raise ValueError("PersonId cannot be empty.")

    @property
    @override
    def id(self) -> str:
        return self._id


@dataclass(eq=False)
class Person(AggregateRoot[PersonId]):
    display_name: str
    given_name: str | None = None
    surname: str | None = None
    mail: str | None = None
