from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.param.int8_param import IntParameterBase

if TYPE_CHECKING:
    from io import BytesIO


class Int64Parameter(IntParameterBase):
    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        return cls(unpack(">q", stream.read(8))[0])

    def serialize(self) -> bytes:
        return pack(">q", self.value)
