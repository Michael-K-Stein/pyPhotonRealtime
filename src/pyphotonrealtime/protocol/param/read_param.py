from __future__ import annotations

from struct import unpack
from typing import TYPE_CHECKING, Any

from pyphotonrealtime.protocol.param.parameter_type import ParameterType

if TYPE_CHECKING:
    from io import BytesIO

    from pyphotonrealtime.protocol.param.base import ParameterBase


def read_parameter(stream: BytesIO, type_code: int | None = None) -> ParameterBase[Any]:
    """Factory function to instantiate the correct ParameterBase subclass."""
    # Deferred: the registry imports every parameter class, which import us.
    from pyphotonrealtime.protocol.param.type_registry import (  # noqa: PLC0415
        TYPE_REGISTRY,
    )

    if type_code is None:
        type_bytes = stream.read(1)
        if not type_bytes:
            msg = "Unexpected end of stream while reading type code."
            raise EOFError(msg)
        type_code = unpack(">B", type_bytes)[0]

    param_type = ParameterType(type_code)
    param_class = TYPE_REGISTRY[param_type]
    return param_class.from_stream(stream)


def get_type_for_instance(instance: ParameterBase[Any]) -> ParameterType:
    """Utility to map an instance back to its Enum type (needed for serialization)."""
    # Deferred: the registry imports every parameter class, which import us.
    from pyphotonrealtime.protocol.param.type_registry import (  # noqa: PLC0415
        TYPE_REGISTRY,
    )

    for p_type, p_class in TYPE_REGISTRY.items():
        if isinstance(instance, p_class):
            return p_type
    msg = f"Unknown parameter class: {type(instance)}"
    raise ValueError(msg)
