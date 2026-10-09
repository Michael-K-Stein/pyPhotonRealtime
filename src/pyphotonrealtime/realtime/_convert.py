"""Convert between protocol ``*Parameter`` objects and plain Python values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyphotonrealtime.protocol.custom_types import (
    custom_type_for_code,
    custom_type_for_value,
)
from pyphotonrealtime.protocol.param.base import ArrayParameterBase, ParameterBase
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.custom_param import CustomParameter
from pyphotonrealtime.protocol.param.dictionary_param_base import (
    DictionaryParameterBase,
)
from pyphotonrealtime.protocol.param.float64_param import DoubleParameter
from pyphotonrealtime.protocol.param.hashtable_param import HashtableParameter
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.int64_param import Int64Parameter
from pyphotonrealtime.protocol.param.nil_param import NilParameter
from pyphotonrealtime.protocol.param.object_slice_param import ObjectSliceParameter
from pyphotonrealtime.protocol.param.parameter_type import ParameterType
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter

if TYPE_CHECKING:
    from collections.abc import Iterable

_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


def to_python(param: ParameterBase[Any]) -> Any:  # noqa: ANN401
    """Recursively convert a parameter into ``dict`` / ``list`` / scalars.

    Returns:
        The plain value.
    """
    if isinstance(param, DictionaryParameterBase):
        return {to_python(k): to_python(v) for k, v in param.items()}
    if isinstance(param, ArrayParameterBase):
        return [to_python(element) for element in param]
    if isinstance(param, CustomParameter):
        custom = custom_type_for_code(param.value["id"])
        if custom is not None:
            return custom.deserialize(param.value["data"])
    return param.value


def to_param(value: object) -> ParameterBase[Any]:
    """Wrap a plain value: ``dict`` -> Hashtable, ``list`` -> object array.

    ``int`` becomes Int32 (Int64 when it doesn't fit) and ``float`` becomes
    Double; instances of types passed to ``register_type`` become custom
    types. Pass a ``*Parameter`` to pick the wire type yourself.

    Returns:
        The parameter.
    """
    match value:
        case ParameterBase():
            return value
        case dict():
            return to_hashtable(value)
        case list() | tuple():
            return ObjectSliceParameter([to_param(v) for v in value])
        case _ if (custom := custom_type_for_value(value)) is not None:
            return CustomParameter({"id": custom.code, "data": custom.serialize(value)})
        case _:
            return _scalar_param(value)


def _scalar_param(value: object) -> ParameterBase[Any]:  # noqa: PLR0911
    match value:
        case None:
            return NilParameter()
        case bool():
            return BooleanParameter(value)
        case int() if _INT32_MIN <= value <= _INT32_MAX:
            return Int32Parameter(value)
        case int():
            return Int64Parameter(value)
        case float():
            return DoubleParameter(value)
        case str():
            return StringParameter(value)
        case bytes() | bytearray():
            return Int8SliceParameter(bytes(value))
    msg = f"Can't send {type(value).__name__} to Photon"
    raise TypeError(msg)


def to_hashtable(values: dict[Any, Any]) -> HashtableParameter[Any, Any]:
    """Wrap a ``dict`` as a Hashtable, converting keys and values.

    Returns:
        The hashtable parameter.
    """
    return HashtableParameter({to_param(k): to_param(v) for k, v in values.items()})


def string_array(values: Iterable[str]) -> SliceParameter[StringParameter]:
    """Wrap strings as a typed ``string[]``.

    Returns:
        The array parameter.
    """
    return SliceParameter(
        [StringParameter(v) for v in values], element_type=ParameterType.StringType
    )
