from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.param.base import ParameterBase
from pyphotonrealtime.protocol.param.string_param import StringParameter

if TYPE_CHECKING:
    from io import BytesIO


class StringSliceParameter(ParameterBase[list[str]]):
    """A typed ``string[]`` (Protocol 1.6 ``'a'``)."""

    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        length = unpack(">h", stream.read(2))[0]
        return cls([StringParameter.from_stream(stream).value for _ in range(length)])

    def serialize(self) -> bytes:
        return pack(">h", len(self.value)) + b"".join(
            StringParameter(value).serialize() for value in self.value
        )

    def __hash__(self) -> int:
        return hash((type(self), tuple(self.value)))
