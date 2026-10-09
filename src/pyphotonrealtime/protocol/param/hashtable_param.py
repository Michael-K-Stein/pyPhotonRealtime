from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Any, Self, cast

from pyphotonrealtime.protocol.param.base import ParameterBase
from pyphotonrealtime.protocol.param.dictionary_param_base import (
    DictionaryParameterBase,
)
from pyphotonrealtime.protocol.param.read_param import (
    get_type_for_instance,
    read_parameter,
)

if TYPE_CHECKING:
    from io import BytesIO


class HashtableParameter[K: ParameterBase[Any], V: ParameterBase[Any]](
    DictionaryParameterBase[K, V]
):
    """Untyped dictionary ('h'). Every key/val has a type prefix."""

    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        length = unpack(">h", stream.read(2))[0]

        # Explicitly declare the dictionary with ParameterBase[Any]
        result: dict[ParameterBase[Any], ParameterBase[Any]] = {}
        for _ in range(length):
            key = read_parameter(stream)
            val = read_parameter(stream)

            # Runtime type validation to catch stream corruption
            if not isinstance(key, ParameterBase):
                msg = f"Expected key to be ParameterBase, got {type(key).__name__}"
                raise TypeError(msg)
            if not isinstance(val, ParameterBase):
                msg = f"Expected value to be ParameterBase, got {type(val).__name__}"
                raise TypeError(msg)

            result[key] = val

        return cls(cast("Any", result))

    def serialize(self) -> bytes:
        payload = bytearray(pack(">h", len(self.value)))

        for key, val in self.value.items():
            payload.append(get_type_for_instance(key))
            payload.extend(key.serialize())

            payload.append(get_type_for_instance(val))
            payload.extend(val.serialize())

        return bytes(payload)
