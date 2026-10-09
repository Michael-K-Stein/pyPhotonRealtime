from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyphotonrealtime.protocol.param.base import ParameterBase

if TYPE_CHECKING:
    from collections.abc import Iterator


class DictionaryParameterBase[K: ParameterBase[Any], V: ParameterBase[Any]](
    ParameterBase[dict[K, V]]
):
    value: dict[K, V]

    def __init__(self, initial_dict: dict[K, V] | None = None) -> None:
        self.value = initial_dict if initial_dict is not None else {}

    def __getitem__(self, key: K) -> V:
        if not isinstance(key, ParameterBase):
            msg = "Key must be derived from ParameterBase!"
            raise TypeError(msg)
        return self.value[key]

    def __setitem__(self, key: K, value: V) -> None:
        if not isinstance(key, ParameterBase):
            msg = "Key must be derived from ParameterBase!"
            raise TypeError(msg)
        if not isinstance(value, ParameterBase):
            msg = "Value must be derived from ParameterBase!"
            raise TypeError(msg)
        self.value[key] = value

    def __delitem__(self, key: K) -> None:
        if not isinstance(key, ParameterBase):
            msg = "Key must be derived from ParameterBase!"
            raise TypeError(msg)
        del self.value[key]

    def __contains__(self, key: object) -> bool:
        return key in self.value

    def __len__(self) -> int:
        return len(self.value)

    def __iter__(self) -> Iterator[K]:
        return iter(self.value)

    def keys(self) -> Iterator[K]:
        return iter(self.value.keys())

    def values(self) -> Iterator[V]:
        return iter(self.value.values())

    def items(self) -> Iterator[tuple[K, V]]:
        return iter(self.value.items())

    def get(self, key: K, default: V | None = None) -> V | None:
        if not isinstance(key, ParameterBase):
            msg = "Key must be derived from ParameterBase!"
            raise TypeError(msg)
        return self.value.get(key, default)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.value})"
