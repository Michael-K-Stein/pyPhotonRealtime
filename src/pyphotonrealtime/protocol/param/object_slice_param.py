from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Any, Self

from pyphotonrealtime.protocol.param.base import ArrayParameterBase, ParameterBase
from pyphotonrealtime.protocol.param.read_param import (
    get_type_for_instance,
    read_parameter,
)

if TYPE_CHECKING:
    from io import BytesIO


class ObjectSliceParameter(ArrayParameterBase[ParameterBase[Any]]):
    """Untyped array (each element has its own type prefix)."""

    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        length = unpack(">h", stream.read(2))[0]
        elements = [read_parameter(stream) for _ in range(length)]
        return cls(elements)

    def serialize(self) -> bytes:
        payload = pack(">h", len(self.value))
        for element in self.value:
            payload += pack(">B", get_type_for_instance(element))
            payload += element.serialize()
        return payload
