from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Any, Self, cast

from pyphotonrealtime.protocol.param.base import ParameterBase
from pyphotonrealtime.protocol.param.dictionary_param_base import (
    DictionaryParameterBase,
)
from pyphotonrealtime.protocol.param.parameter_type import ParameterType
from pyphotonrealtime.protocol.param.read_param import (
    get_type_for_instance,
    read_parameter,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from io import BytesIO


class DictionaryParameter[K: ParameterBase[Any], V: ParameterBase[Any]](
    DictionaryParameterBase[K, V]
):
    """Strongly typed dictionary ('D'). Type codes are declared ONCE in the header."""

    type_header: bytes | None = None
    """Protocol 1.8 type header as received, so a relay sends the same types."""

    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        key_type = unpack(">B", stream.read(1))[0]
        val_type = unpack(">B", stream.read(1))[0]
        length = unpack(">h", stream.read(2))[0]

        # 0 (unknown) and '*' (object) mean every entry carries its own type.
        key_type = None if key_type in _OBJECT_TYPES else key_type
        val_type = None if val_type in _OBJECT_TYPES else val_type

        result: dict[ParameterBase[Any], ParameterBase[Any]] = {}
        for _ in range(length):
            key = read_parameter(stream, key_type)
            val = read_parameter(stream, val_type)

            result[key] = val

        return cls(cast("Any", result))

    def serialize(self) -> bytes:
        if not self.value:
            return pack(">BBh", ParameterType.NilType, ParameterType.NilType, 0)

        key_type = _common_type(self.value.keys())
        val_type = _common_type(self.value.values())

        payload = bytearray(
            pack(">BBh", key_type or _OBJECT, val_type or _OBJECT, len(self.value))
        )

        for key, val in self.value.items():
            if key_type is None:
                payload.append(get_type_for_instance(key))
            payload.extend(key.serialize())
            if val_type is None:
                payload.append(get_type_for_instance(val))
            payload.extend(val.serialize())

        return bytes(payload)


def _common_type(params: Iterable[ParameterBase[Any]]) -> ParameterType | None:
    """The one type shared by ``params``; None when mixed (sent as ``object``)."""
    types = {get_type_for_instance(param) for param in params}
    if len(types) != 1:
        return None
    (common,) = types
    # Containers and custom types can't be declared once in the header.
    return None if common in _UNTYPED else common


_OBJECT = 0
_OBJECT_TYPES = {_OBJECT, ParameterType.NilType}
_UNTYPED = {
    ParameterType.NilType,
    ParameterType.Custom,
    ParameterType.DictionaryType,
    ParameterType.SliceType,
}
