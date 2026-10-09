from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from io import BytesIO


class ParameterBase[V](ABC):
    """Base class for all Photon parameters."""

    value: V

    def __init__(self, value: V) -> None:
        self.value = value

    @classmethod
    @abstractmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        """Reads from the byte stream and constructs the parameter object."""

    @abstractmethod
    def serialize(self) -> bytes:
        """Converts the internal value back into a byte payload."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.value!r})"

    def __hash__(self) -> int:
        return hash((type(self), self.value))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, type(self)):
            return False
        return self.value == other.value


class ArrayParameterBase[T: ParameterBase[Any]](ParameterBase[list[T]]):
    """Base class for Photon Array/Slice parameters."""

    def __init__(self, value: Sequence[T] | None = None) -> None:
        # Default to an empty list if nothing is provided
        super().__init__(list(value) if value is not None else [])

    def __setitem__(self, key: int, value: T) -> None:
        if not isinstance(key, int):
            msg = f"Key must be an index, got {type(key).__name__}"
            raise TypeError(msg)
        if not isinstance(value, ParameterBase):
            msg = (
                f"Value must be derived from ParameterBase, got {type(value).__name__}"
            )
            raise TypeError(msg)
        self.value[key] = value

    def __getitem__(self, key: int) -> T:
        if not isinstance(key, int):
            msg = f"Key must be an index, got {type(key).__name__}"
            raise TypeError(msg)
        return self.value[key]

    def __len__(self) -> int:
        return len(self.value)

    def __iter__(self) -> Iterator[T]:
        return iter(self.value)

    def append(self, value: T) -> None:
        if not isinstance(value, ParameterBase):
            msg = (
                f"Value must be derived from ParameterBase, got {type(value).__name__}"
            )
            raise TypeError(msg)
        self.value.append(value)
