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
    from io import BytesIO


class DictionaryParameter[K: ParameterBase[Any], V: ParameterBase[Any]](
    DictionaryParameterBase[K, V]
):
    """Strongly typed dictionary ('D'). Type codes are declared ONCE in the header."""

    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        key_type = unpack(">B", stream.read(1))[0]
        val_type = unpack(">B", stream.read(1))[0]
        length = unpack(">h", stream.read(2))[0]

        result: dict[ParameterBase[Any], ParameterBase[Any]] = {}
        for _ in range(length):
            key = read_parameter(stream, key_type)
            val = read_parameter(stream, val_type)

            result[key] = val

        return cls(cast("Any", result))

    def serialize(self) -> bytes:
        if not self.value:
            return pack(">BBh", ParameterType.NilType, ParameterType.NilType, 0)

        first_key, first_val = next(iter(self.value.items()))

        key_type = get_type_for_instance(first_key)
        val_type = get_type_for_instance(first_val)

        payload = bytearray(pack(">BBh", key_type, val_type, len(self.value)))

        for key, val in self.value.items():
            payload.extend(key.serialize())
            payload.extend(val.serialize())

        return bytes(payload)
