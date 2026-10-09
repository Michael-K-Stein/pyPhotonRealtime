from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.param.base import ParameterBase

if TYPE_CHECKING:
    from io import BytesIO


class StringParameter(ParameterBase[str]):
    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            msg = "Value of StringParameter must be a string!"
            raise TypeError(msg)
        super().__init__(value)

    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        length = unpack(">h", stream.read(2))[0]
        if length == 0:
            return cls("")
        return cls(stream.read(length).decode("utf-8"))

    def serialize(self) -> bytes:
        encoded = self.value.encode("utf-8")
        return pack(">h", len(encoded)) + encoded
