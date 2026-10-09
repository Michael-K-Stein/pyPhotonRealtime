"""Protocol 1.8 codec, the 1.6 gaps it closed, and custom types."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast

import pytest

from pyphotonrealtime.protocol import protocol18
from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.custom_types import (
    custom_type_for_code,
    register_type,
    unregister_type,
)
from pyphotonrealtime.protocol.packet.factory import PacketFactory
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.packet.packet_stream import PhotonStreamParser
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.custom_param import CustomParameter
from pyphotonrealtime.protocol.param.dict_param import DictionaryParameter
from pyphotonrealtime.protocol.param.float32_param import Float32Parameter
from pyphotonrealtime.protocol.param.float64_param import DoubleParameter
from pyphotonrealtime.protocol.param.hashtable_param import HashtableParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.int16_param import Int16Parameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.int32_slice_param import Int32SliceParameter
from pyphotonrealtime.protocol.param.int64_param import Int64Parameter
from pyphotonrealtime.protocol.param.nil_param import NilParameter
from pyphotonrealtime.protocol.param.object_slice_param import ObjectSliceParameter
from pyphotonrealtime.protocol.param.parameter_type import ParameterType
from pyphotonrealtime.protocol.param.read_param import (
    get_type_for_instance,
    read_parameter,
)
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.protocol.param.string_slice_param import StringSliceParameter
from pyphotonrealtime.protocol.protocol18 import (
    GpType18,
    read_compressed_uint,
    read_value,
    write_compressed_uint,
    write_value,
)
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol
from pyphotonrealtime.realtime._convert import to_param, to_python

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyphotonrealtime.protocol.enum_lookups import CommandParams
    from pyphotonrealtime.protocol.operation_code import OperationCode
    from pyphotonrealtime.protocol.param.base import ParameterBase


def _decode(data: bytes) -> ParameterBase[Any]:
    stream = BytesIO(data)
    value = read_value(stream)
    assert stream.read() == b"", "trailing bytes"
    return value


def _typed[T](elements: list[T], element_type: ParameterType) -> Any:  # noqa: ANN401
    return SliceParameter(cast("Any", elements), element_type=element_type)


# -- encodings that match the SDKs byte for byte ------------------------------


@pytest.mark.parametrize(
    ("param", "encoded"),
    [
        (NilParameter(), "08"),
        (BooleanParameter(value=True), "1c"),
        (BooleanParameter(value=False), "1b"),
        (Int8Parameter(0), "22"),
        (Int8Parameter(200), "03c8"),
        (Int16Parameter(0), "1d"),
        (Int16Parameter(-2), "04feff"),
        (Int32Parameter(0), "1e"),
        (Int32Parameter(7), "0b07"),
        (Int32Parameter(-7), "0c07"),
        (Int32Parameter(684), "0dac02"),
        (Int32Parameter(-684), "0eac02"),
        (Int32Parameter(70000), "09e0c508"),  # zig-zag 140000
        (Int32Parameter(-70000), "09dfc508"),
        (Int64Parameter(0), "1f"),
        (Int64Parameter(255), "0fff"),
        (Int64Parameter(-65535), "12ffff"),
        (Int64Parameter(2**40), "0a808080808040"),
        (Float32Parameter(0.0), "20"),
        (Float32Parameter(1.5), "050000c03f"),
        (DoubleParameter(0.0), "21"),
        (DoubleParameter(-2.0), "0600000000000000c0"),
        (StringParameter("hi"), "07026869"),
        (StringParameter("é"), "0702c3a9"),
        (Int8SliceParameter(b"\x01\x02"), "43020102"),
        (CustomParameter({"id": 86, "data": b"ab"}), "d6026162"),  # slim: 0x80|86
        (CustomParameter({"id": 200, "data": b"ab"}), "13c8026162"),
        (ObjectSliceParameter([Int32Parameter(1), NilParameter()]), "17020b0108"),
        (HashtableParameter({StringParameter("k"): Int32Parameter(0)}), "150107016b1e"),
    ],
    ids=repr,
)
def test_value_encoding(param: ParameterBase[Any], encoded: str) -> None:
    assert write_value(param).hex() == encoded
    assert _decode(bytes.fromhex(encoded)) == param


@pytest.mark.parametrize(
    ("value", "encoded"),
    [(0, "00"), (127, "7f"), (128, "8001"), (300, "ac02"), (2**32 - 1, "ffffffff0f")],
)
def test_compressed_uint(value: int, encoded: str) -> None:
    assert write_compressed_uint(value).hex() == encoded
    assert read_compressed_uint(BytesIO(bytes.fromhex(encoded))) == value


def test_compressed_uint_needs_all_its_bytes() -> None:
    with pytest.raises(EOFError):
        read_compressed_uint(BytesIO(b"\x80"))


def test_ping_response_captured_from_photon_cloud() -> None:
    # A Name Server's answer to the WebSocket ping operation, as received.
    data = bytes.fromhex("f3070100000802010dac02020999ed84b205")
    parser = PhotonStreamParser(SerializationProtocol.V18)
    parser.feed(bytes([0xFB]) + struct.pack(">I", len(data) + 7) + b"\x00\x01" + data)
    (packet,) = list(parser.parse(expect_responses=True))
    assert isinstance(packet, PhotonOperationPacket)
    payload = packet.get_payload()
    assert packet.get_header().get_command_code() == CommandCode.KeyExchangeResponse
    assert int(payload.operation_code) == 1
    assert payload.get_return_code() == 0
    assert payload.get_debug_message() is None
    assert payload.params[cast("Any", 1)] == Int32Parameter(684)


# -- round trips --------------------------------------------------------------


ROUND_TRIPS: list[ParameterBase[Any]] = [
    _typed(
        [BooleanParameter(value=b) for b in [True] * 9 + [False]],
        ParameterType.BooleanType,
    ),
    _typed([Int16Parameter(1), Int16Parameter(-300)], ParameterType.Int16Type),
    _typed([Float32Parameter(0.5)], ParameterType.Float32Type),
    _typed([DoubleParameter(0.25), DoubleParameter(0.0)], ParameterType.DoubleType),
    _typed([StringParameter("a"), StringParameter("")], ParameterType.StringType),
    _typed([Int32Parameter(0), Int32Parameter(-(2**31))], ParameterType.Int32Type),
    _typed([Int64Parameter(2**63 - 1)], ParameterType.Int64Type),
    _typed(
        [
            CustomParameter({"id": 7, "data": b"x"}),
            CustomParameter({"id": 7, "data": b""}),
        ],
        ParameterType.Custom,
    ),
    _typed(
        [HashtableParameter({Int8Parameter(1): StringParameter("v")})],
        ParameterType.Hashtable,
    ),
    _typed(
        [
            DictionaryParameter(cast("Any", {StringParameter("a"): Int32Parameter(1)})),
            DictionaryParameter(
                cast("Any", {StringParameter("b"): Int32Parameter(-1)})
            ),
        ],
        ParameterType.DictionaryType,
    ),
    DictionaryParameter(cast("Any", {Int8Parameter(1): Int8SliceParameter(b"secret")})),
    DictionaryParameter(
        cast(
            "Any",
            {StringParameter("a"): Int32Parameter(1), Int8Parameter(2): NilParameter()},
        )
    ),
    DictionaryParameter(
        cast(
            "Any",
            {
                StringParameter("nested"): DictionaryParameter(
                    cast("Any", {Int32Parameter(1): BooleanParameter(value=True)})
                )
            },
        )
    ),
    HashtableParameter(
        {
            Int8Parameter(255): ObjectSliceParameter(
                [StringParameter("deep"), HashtableParameter({})]
            ),
            StringParameter("custom"): CustomParameter({"id": 120, "data": b"\x00"}),
        }
    ),
    SliceParameter(
        [Int8SliceParameter(b"ab"), Int8SliceParameter(b"")]
    ),  # Array of arrays.
]


@pytest.mark.parametrize("param", ROUND_TRIPS, ids=lambda p: type(p).__name__)
def test_round_trip(param: ParameterBase[Any]) -> None:
    decoded = _decode(write_value(param))
    assert to_python(decoded) == to_python(param)
    assert type(decoded) is type(param)


@pytest.mark.parametrize(
    ("param", "decoded"),
    [
        (StringSliceParameter(["a", "b"]), ["a", "b"]),
        (Int32SliceParameter([1, -2]), [1, -2]),
        (
            _typed([Int8Parameter(1), Int8Parameter(2)], ParameterType.Int8Type),
            b"\x01\x02",
        ),
    ],
)
def test_arrays_decode_to_their_1_6_shape(
    param: ParameterBase[Any], decoded: object
) -> None:
    assert to_python(_decode(write_value(param))) == decoded


def test_dictionary_header_types_values_once() -> None:
    dictionary = DictionaryParameter(
        cast("Any", {StringParameter("a"): Int32Parameter(1)})
    )
    # Key type String, value type CompressedInt, one entry: "a" -> zig-zag 1.
    assert write_value(dictionary).hex() == "14070901016102"


def test_unknown_type_code_is_an_error() -> None:
    with pytest.raises(ValueError, match=r"Unknown Protocol 1.8 type code"):
        read_value(BytesIO(b"\x01"))


def test_too_many_parameters() -> None:
    with pytest.raises(ValueError, match="255"):
        protocol18.write_parameter_table({key: NilParameter() for key in range(256)})


@pytest.mark.parametrize("protocol", list(SerializationProtocol))
def test_operation_packets_round_trip_in_both_protocols(
    protocol: SerializationProtocol,
) -> None:
    params = cast(
        "CommandParams",
        {
            245: to_param({"x": [1, 2.5, None, "s", b"raw"], 3: {"nested": True}}),
            1: Int16Parameter(-1),
        },
    )
    packets = [
        PacketFactory.operation(
            CommandCode.Operation,
            operation=cast("OperationCode", 253),
            params=params,
            protocol=protocol,
        ),
        PacketFactory.operation(
            CommandCode.OperationResponse,
            operation=cast("OperationCode", 253),
            params=params,
            return_code=-2,
            error_message="nope",
            protocol=protocol,
        ),
    ]
    parser = PhotonStreamParser(protocol)
    parser.feed(b"".join(packet.serialize() for packet in packets))
    request, response = parser.parse(expect_responses=True)
    assert isinstance(request, PhotonOperationPacket)
    assert isinstance(response, PhotonOperationPacket)
    for packet in (request, response):
        assert {
            int(k): to_python(v) for k, v in packet.get_payload().params.items()
        } == {245: {"x": [1, 2.5, None, "s", b"raw"], 3: {"nested": True}}, 1: -1}
    assert response.get_payload().get_return_code() == -2
    assert response.get_payload().get_debug_message() == "nope"


# -- Protocol 1.6 gaps ----------------------------------------------------------


@pytest.mark.parametrize(
    "param",
    [
        Int16Parameter(-12345),
        StringSliceParameter(["x", "yz"]),
        Int32SliceParameter([1, -1, 2**31 - 1]),
    ],
    ids=repr,
)
def test_new_1_6_types_round_trip(param: ParameterBase[Any]) -> None:
    code = get_type_for_instance(param)
    assert read_parameter(BytesIO(param.serialize()), code) == param


def test_1_6_dictionary_with_object_values() -> None:
    # Mixed value types go out as 'object' and every value carries its type.
    dictionary = DictionaryParameter(
        cast(
            "Any",
            {
                Int8Parameter(1): StringParameter("a"),
                Int8Parameter(2): Int32Parameter(3),
            },
        )
    )
    data = dictionary.serialize()
    assert data[:2] == bytes([ParameterType.Int8Type, 0])
    decoded = read_parameter(BytesIO(data), ParameterType.DictionaryType)
    assert to_python(decoded) == {1: "a", 2: 3}


def test_1_6_custom_type_array_sends_the_type_id_once() -> None:
    array = _typed(
        [
            CustomParameter({"id": 9, "data": b"ab"}),
            CustomParameter({"id": 9, "data": b"c"}),
        ],
        ParameterType.Custom,
    )
    data = array.serialize()
    assert (
        data == struct.pack(">hBB", 2, ParameterType.Custom, 9) + b"\x00\x02ab\x00\x01c"
    )
    decoded = read_parameter(BytesIO(data), ParameterType.SliceType)
    assert decoded == array


# -- custom types -------------------------------------------------------------


@dataclass
class Point:
    x: int
    y: int


@pytest.fixture
def point_type() -> Iterator[None]:
    register_type(
        Point,
        code=ord("P"),
        serialize=lambda p: struct.pack(">ii", p.x, p.y),
        deserialize=lambda data: Point(*struct.unpack(">ii", data)),
    )
    yield
    unregister_type(Point)


@pytest.mark.usefixtures("point_type")
@pytest.mark.parametrize("protocol", list(SerializationProtocol))
def test_custom_type_round_trip(protocol: SerializationProtocol) -> None:
    value = {"at": Point(1, -2), "path": [Point(0, 0), Point(3, 4)]}
    param = to_param(value)
    data = (
        write_value(param)
        if protocol == SerializationProtocol.V18
        else (bytes([get_type_for_instance(param)]) + param.serialize())
    )
    stream = BytesIO(data)
    decoded = (
        read_value(stream)
        if protocol == SerializationProtocol.V18
        else read_parameter(stream)
    )
    assert to_python(decoded) == value


@pytest.mark.usefixtures("point_type")
def test_custom_type_registry_rejects_clashes() -> None:
    custom = custom_type_for_code(ord("P"))
    assert custom is not None
    assert custom.cls is Point
    with pytest.raises(ValueError, match="taken"):
        register_type(dict, ord("P"), lambda _: b"", lambda _: {})
    with pytest.raises(ValueError, match="already registered"):
        register_type(Point, 1, lambda _: b"", lambda _: Point(0, 0))
    with pytest.raises(ValueError, match="0-255"):
        register_type(set, 256, lambda _: b"", lambda _: set())


def test_unregistered_custom_values_stay_raw() -> None:
    assert unregister_type(Point) is False
    raw = CustomParameter({"id": 42, "data": b"?"})
    assert to_python(raw) == {"id": 42, "data": b"?"}
    assert GpType18.CustomTypeSlim | 42 == write_value(raw)[0]
