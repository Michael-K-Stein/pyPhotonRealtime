from __future__ import annotations

from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.param.base import ParameterBase

if TYPE_CHECKING:
    from io import BytesIO


class NilParameter(ParameterBase[None]):
    def __init__(self, value: None = None) -> None:
        super().__init__(value)

    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:  # noqa: ARG003 - nil has no payload
        return cls(None)

    def serialize(self) -> bytes:
        return b""
