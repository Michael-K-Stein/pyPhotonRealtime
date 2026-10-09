from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.param.base import ParameterBase

if TYPE_CHECKING:
    from io import BytesIO


class Int32SliceParameter(ParameterBase[list[int]]):
    """A typed ``int[]`` (Protocol 1.6 ``'n'``, Protocol 1.8 compressed ints)."""

    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        length = unpack(">i", stream.read(4))[0]
        return cls(list(unpack(f">{length}i", stream.read(4 * length))))

    def serialize(self) -> bytes:
        return pack(f">i{len(self.value)}i", len(self.value), *self.value)

    def __hash__(self) -> int:
        return hash((type(self), tuple(self.value)))
