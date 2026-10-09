from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.param.base import ParameterBase

if TYPE_CHECKING:
    from io import BytesIO


class BooleanParameter(ParameterBase[bool]):
    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        val = unpack(">B", stream.read(1))[0]
        return cls(val != 0)

    def serialize(self) -> bytes:
        return pack(">B", 1 if self.value else 0)
