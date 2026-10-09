from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.custom_param import CustomParameter
from pyphotonrealtime.protocol.param.dict_param import DictionaryParameter
from pyphotonrealtime.protocol.param.float32_param import Float32Parameter
from pyphotonrealtime.protocol.param.float64_param import DoubleParameter
from pyphotonrealtime.protocol.param.hashtable_param import HashtableParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.int64_param import Int64Parameter
from pyphotonrealtime.protocol.param.nil_param import NilParameter
from pyphotonrealtime.protocol.param.object_slice_param import ObjectSliceParameter
from pyphotonrealtime.protocol.param.parameter_type import ParameterType
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.param.base import ParameterBase

TYPE_REGISTRY: dict[ParameterType, type[ParameterBase[Any]]] = {
    ParameterType.NilType: NilParameter,
    ParameterType.BooleanType: BooleanParameter,
    ParameterType.Int8Type: Int8Parameter,
    ParameterType.Int32Type: Int32Parameter,
    ParameterType.Int64Type: Int64Parameter,
    ParameterType.Float32Type: Float32Parameter,
    ParameterType.DoubleType: DoubleParameter,
    ParameterType.StringType: StringParameter,
    ParameterType.Int8SliceType: Int8SliceParameter,
    ParameterType.SliceType: SliceParameter,
    ParameterType.ObjectSliceType: ObjectSliceParameter,
    ParameterType.DictionaryType: DictionaryParameter,
    ParameterType.Hashtable: HashtableParameter,
    ParameterType.Custom: CustomParameter,
}
