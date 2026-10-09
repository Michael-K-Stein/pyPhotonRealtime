from __future__ import annotations

from struct import pack, unpack
from typing import TYPE_CHECKING, Any, Self, cast

from pyphotonrealtime.protocol.param.base import ArrayParameterBase, ParameterBase
from pyphotonrealtime.protocol.param.parameter_type import ParameterType
from pyphotonrealtime.protocol.param.read_param import (
    get_type_for_instance,
    read_parameter,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from io import BytesIO


class SliceParameter[T: ParameterBase[Any]](ArrayParameterBase[T]):
    """Strongly typed array."""

    def __init__(
        self, elements: Sequence[T], element_type: ParameterType | None = None
    ) -> None:
        self.element_type = element_type
        super().__init__(elements)

    @classmethod
    def from_stream(cls, stream: BytesIO) -> Self:
        length = unpack(">h", stream.read(2))[0]
        element_type = unpack(">B", stream.read(1))[0]
        elements = [read_parameter(stream, element_type) for _ in range(length)]
        # Cast the list to Any to satisfy the strict T requirement of the constructor
        return cls(cast("Any", elements), element_type=element_type)

    def serialize(self) -> bytes:
        if not self.value:
            return pack(
                ">hB",
                0,
                (
                    self.element_type
                    if self.element_type is not None
                    else ParameterType.NilType
                ),
            )

        element_type = get_type_for_instance(self.value[0])
        if self.element_type is not None and element_type != self.element_type:
            msg = (
                f"Hardcoded element type ({self.element_type}) does not match "
                f"runtime element type ({element_type})."
            )
            raise TypeError(msg)
        payload = pack(">hB", len(self.value), element_type)
        for element in self.value:
            payload += element.serialize()
        return payload
