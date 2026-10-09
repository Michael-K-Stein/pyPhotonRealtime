from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.param.base import ParameterBase

if TYPE_CHECKING:
    from io import BytesIO


class DoubleParameter(ParameterBase[float]):
    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        return cls(unpack(">d", stream.read(8))[0])

    def serialize(self) -> bytes:
        return pack(">d", self.value)
