from dataclasses import dataclass
from typing import override

from domain.models.base import ID, AggregateRoot


@dataclass(frozen=True)
class PersonId(ID[str]):
    """Identify a person with a nonblank string value."""

    _id: str

    def __post_init__(self) -> None:
        """Reject identifiers that are empty or contain only whitespace.

        Raises:
            ValueError: If the identifier is blank.
        """
        if not self._id.strip():
            raise ValueError("PersonId cannot be empty.")

    @property
    @override
    def id(self) -> str:
        """Return the identifier string."""
        return self._id


@dataclass(eq=False)
class Person(AggregateRoot[PersonId]):
    """Represent a person by a typed ID and optional profile details."""

    display_name: str
    given_name: str | None = None
    surname: str | None = None
    mail: str | None = None
