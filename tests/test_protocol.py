"""Round-trip tests for the wire layer inherited from prison-architect-server."""

from io import BytesIO

import pytest

from pyphotonrealtime.protocol.param.base import ParameterBase
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.read_param import (
    get_type_for_instance,
    read_parameter,
)
from pyphotonrealtime.protocol.param.string_param import StringParameter


@pytest.mark.parametrize(
    "param",
    [
        BooleanParameter(True),
        Int32Parameter(-123456),
        StringParameter("hello photon"),
    ],
)
def test_parameter_round_trip(param: ParameterBase) -> None:
    type_code = get_type_for_instance(param)
    decoded = read_parameter(BytesIO(param.serialize()), type_code.value)
    assert decoded == param
