from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.param.base import ParameterBase

if TYPE_CHECKING:
    from io import BytesIO


class Int8SliceParameter(ParameterBase[bytes]):
    """Represents a raw Byte Array."""

    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        length = unpack(">i", stream.read(4))[0]
        return cls(stream.read(length))

    def serialize(self) -> bytes:
        return pack(">i", len(self.value)) + self.value
