from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, override


@dataclass(frozen=True)
class ID[T](ABC):
    """Represent a domain identifier with a dedicated value type."""

    @property
    @abstractmethod
    def id(self) -> T:
        """Return the identifier's underlying value."""
        ...


@dataclass(frozen=True)
class ValueObject(ABC):
    """Mark types that model domain values."""


@dataclass(eq=False)
class AggregateRoot[T: ID[Any]](ABC):
    """Compare aggregate roots by their domain identifiers."""

    _id: T

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, self.__class__):
            return NotImplemented
        return self.id == other.id

    @override
    def __hash__(self) -> int:
        return hash(self.id)

    @property
    def id(self) -> T:
        """Return this aggregate's identifier."""
        return self._id
