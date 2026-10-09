from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Self, TypedDict

from pyphotonrealtime.protocol.param.base import ParameterBase

if TYPE_CHECKING:
    from io import BytesIO


class CustomValue(TypedDict):
    """A registered custom type: its one-byte type id and its raw payload."""

    id: int
    data: bytes


class CustomParameter(ParameterBase[CustomValue]):
    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        custom_id: int = unpack(">B", stream.read(1))[0]
        length: int = unpack(">h", stream.read(2))[0]
        data = stream.read(length)
        return cls({"id": custom_id, "data": data})

    def serialize(self) -> bytes:
        data = self.value["data"]
        return pack(">Bh", self.value["id"], len(data)) + data
