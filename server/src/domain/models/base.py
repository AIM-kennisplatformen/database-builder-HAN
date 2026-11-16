from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, override


@dataclass(frozen=True)
class ID[T](ABC):
    @property
    @abstractmethod
    def id(self) -> T: ...


@dataclass(frozen=True)
class ValueObject(ABC): ...


@dataclass(eq=False)
class AggregateRoot[T: ID[Any]](ABC):
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
        return self._id
