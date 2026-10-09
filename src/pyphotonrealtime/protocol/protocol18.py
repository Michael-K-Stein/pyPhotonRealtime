"""Protocol 1.8 (``GpBinaryV18``): the serialization the v4.1.6+ and v5 SDKs use.

It carries the same values as Protocol 1.6 but encodes them more compactly:
little-endian numbers, variable-length (zig-zag) integers, type codes that
carry small values (``IntZero``, ``BooleanTrue``, ``Int1``...) and a one-byte
parameter count. Values are the same ``*Parameter`` classes as for 1.6, so
everything above the codec is protocol-agnostic.
"""

from __future__ import annotations

import struct
from enum import IntEnum
from typing import TYPE_CHECKING, Any, cast

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
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.protocol.param.string_slice_param import StringSliceParameter

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from io import BytesIO

    from pyphotonrealtime.protocol.param.base import ParameterBase


class GpType18(IntEnum):
    """Protocol 1.8 type codes (``TypeCode18`` in the C++ SDK)."""

    Unknown = 0
    Boolean = 2
    Byte = 3
    Short = 4
    Float = 5
    Double = 6
    String = 7
    Null = 8
    CompressedInt = 9
    CompressedLong = 10
    Int1 = 11
    Int1Negative = 12
    Int2 = 13
    Int2Negative = 14
    Long1 = 15
    Long1Negative = 16
    Long2 = 17
    Long2Negative = 18
    Custom = 19
    Dictionary = 20
    Hashtable = 21
    ObjectArray = 23
    OperationRequest = 24
    OperationResponse = 25
    EventData = 26
    BooleanFalse = 27
    BooleanTrue = 28
    ShortZero = 29
    IntZero = 30
    LongZero = 31
    FloatZero = 32
    DoubleZero = 33
    ByteZero = 34
    Array = 0x40
    BooleanArray = 0x42
    ByteArray = 0x43
    ShortArray = 0x44
    FloatArray = 0x45
    DoubleArray = 0x46
    StringArray = 0x47
    CompressedIntArray = 0x49
    CompressedLongArray = 0x4A
    CustomTypeArray = 0x53
    DictionaryArray = 0x54
    HashtableArray = 0x55
    CustomTypeSlim = 0x80
    """``0x80 | id``: a custom type with ``id < 100``, without a separate id byte."""


_CUSTOM_SLIM_RANGE = 100
_BYTE_MAX = 0xFF
_USHORT_MAX = 0xFFFF
_BITS_PER_BYTE = 8

# Element type of a 1.6 typed array -> its 1.8 array code.
_ARRAY_CODES = {
    ParameterType.BooleanType: GpType18.BooleanArray,
    ParameterType.Int8Type: GpType18.ByteArray,
    ParameterType.Int16Type: GpType18.ShortArray,
    ParameterType.Float32Type: GpType18.FloatArray,
    ParameterType.DoubleType: GpType18.DoubleArray,
    ParameterType.StringType: GpType18.StringArray,
    ParameterType.Int32Type: GpType18.CompressedIntArray,
    ParameterType.Int64Type: GpType18.CompressedLongArray,
    ParameterType.Custom: GpType18.CustomTypeArray,
    ParameterType.DictionaryType: GpType18.DictionaryArray,
    ParameterType.Hashtable: GpType18.HashtableArray,
}


# -- primitives ---------------------------------------------------------------


def write_compressed_uint(value: int) -> bytes:
    """Encode an unsigned integer as a little-endian base-128 varint.

    Returns:
        The encoded bytes.
    """
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def read_compressed_uint(stream: BytesIO) -> int:
    """Decode a varint written by :func:`write_compressed_uint`.

    Returns:
        The value.

    Raises:
        EOFError: The stream ended inside the varint.
    """
    value = shift = 0
    while True:
        raw = stream.read(1)
        if not raw:
            msg = "Unexpected end of stream inside a compressed integer."
            raise EOFError(msg)
        value |= (raw[0] & 0x7F) << shift
        if raw[0] < 0x80:  # noqa: PLR2004 - continuation bit
            return value
        shift += 7


def _zigzag(value: int, bits: int) -> int:
    return ((value << 1) ^ (value >> (bits - 1))) & ((1 << bits) - 1)


def _unzigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _read(stream: BytesIO, fmt: str) -> Any:  # noqa: ANN401
    size = struct.calcsize(fmt)
    data = stream.read(size)
    if len(data) != size:
        msg = "Unexpected end of stream."
        raise EOFError(msg)
    return struct.unpack(fmt, data)[0]


def _write_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return write_compressed_uint(len(encoded)) + encoded


def _read_string(stream: BytesIO) -> str:
    return stream.read(read_compressed_uint(stream)).decode("utf-8")


# -- writing ------------------------------------------------------------------


def write_value(param: ParameterBase[Any]) -> bytes:
    """Serialize a parameter with its type code (``Write(value, setType=true)``).

    Returns:
        Type code and body.
    """
    compact = _write_compact(param)
    if compact is not None:
        return compact
    code = type_code(param)
    return bytes([code]) + _write_body(param, code)


def _write_compact(param: ParameterBase[Any]) -> bytes | None:  # noqa: PLR0911
    """Encode values that 1.8 folds into the type code; None for the rest.

    Returns:
        The encoded value, or None.
    """
    match param:
        case BooleanParameter():
            return bytes(
                [GpType18.BooleanTrue if param.value else GpType18.BooleanFalse]
            )
        case Int8Parameter() if param.value == 0:
            return bytes([GpType18.ByteZero])
        case Int16Parameter() if param.value == 0:
            return bytes([GpType18.ShortZero])
        case Float32Parameter() if param.value == 0:
            return bytes([GpType18.FloatZero])
        case DoubleParameter() if param.value == 0:
            return bytes([GpType18.DoubleZero])
        case Int32Parameter():
            return _write_small_int(param.value, long=False)
        case Int64Parameter():
            return _write_small_int(param.value, long=True)
        case CustomParameter() if param.value["id"] < _CUSTOM_SLIM_RANGE:
            data = param.value["data"]
            return (
                bytes([GpType18.CustomTypeSlim | param.value["id"]])
                + write_compressed_uint(len(data))
                + data
            )
    return None


def _write_small_int(value: int, *, long: bool) -> bytes:
    if value == 0:
        return bytes([GpType18.LongZero if long else GpType18.IntZero])
    magnitude = abs(value)
    if magnitude <= _BYTE_MAX:
        codes = (
            GpType18.Long1,
            GpType18.Long1Negative,
            GpType18.Int1,
            GpType18.Int1Negative,
        )
        return bytes([codes[(0 if long else 2) + (value < 0)], magnitude])
    if magnitude <= _USHORT_MAX:
        codes = (
            GpType18.Long2,
            GpType18.Long2Negative,
            GpType18.Int2,
            GpType18.Int2Negative,
        )
        return bytes([codes[(0 if long else 2) + (value < 0)]]) + struct.pack(
            "<H", magnitude
        )
    code = GpType18.CompressedLong if long else GpType18.CompressedInt
    return bytes([code]) + write_compressed_uint(_zigzag(value, 64 if long else 32))


def type_code(param: ParameterBase[Any]) -> GpType18:  # noqa: C901, PLR0911, PLR0912
    """The 1.8 type code a parameter is written with when it's typed once.

    Returns:
        The type code (the non-compact one, e.g. ``CompressedInt`` for ints).

    Raises:
        TypeError: ``param`` is not a known parameter class.
    """
    match param:
        case NilParameter():
            return GpType18.Null
        case BooleanParameter():
            return GpType18.Boolean
        case Int8Parameter():
            return GpType18.Byte
        case Int16Parameter():
            return GpType18.Short
        case Int32Parameter():
            return GpType18.CompressedInt
        case Int64Parameter():
            return GpType18.CompressedLong
        case Float32Parameter():
            return GpType18.Float
        case DoubleParameter():
            return GpType18.Double
        case StringParameter():
            return GpType18.String
        case CustomParameter():
            return GpType18.Custom
        case DictionaryParameter():
            return GpType18.Dictionary
        case HashtableParameter():
            return GpType18.Hashtable
        case ObjectSliceParameter():
            return GpType18.ObjectArray
        case Int8SliceParameter():
            return GpType18.ByteArray
        case Int32SliceParameter():
            return GpType18.CompressedIntArray
        case StringSliceParameter():
            return GpType18.StringArray
        case SliceParameter():
            return _array_code(param)
    msg = f"Unknown parameter class: {type(param).__name__}"
    raise TypeError(msg)


def _array_code(param: SliceParameter[Any]) -> GpType18:
    from pyphotonrealtime.protocol.param.read_param import (  # noqa: PLC0415
        get_type_for_instance,
    )

    element_type = (
        get_type_for_instance(param.value[0]) if param.value else param.element_type
    )
    if element_type is None:
        return GpType18.ObjectArray
    return _ARRAY_CODES.get(ParameterType(element_type), GpType18.Array)


def _write_body(param: ParameterBase[Any], code: int) -> bytes:  # noqa: C901, PLR0911, PLR0912
    """Serialize a value without its type code (typed arrays and dictionaries).

    Returns:
        The body.
    """
    match code:
        case GpType18.Null:
            return b""
        case GpType18.Boolean:
            return b"\x01" if param.value else b"\x00"
        case GpType18.Byte:
            return bytes([param.value & 0xFF])
        case GpType18.Short:
            return struct.pack("<h", param.value)
        case GpType18.Float:
            return struct.pack("<f", param.value)
        case GpType18.Double:
            return struct.pack("<d", param.value)
        case GpType18.String:
            return _write_string(param.value)
        case GpType18.CompressedInt:
            return write_compressed_uint(_zigzag(param.value, 32))
        case GpType18.CompressedLong:
            return write_compressed_uint(_zigzag(param.value, 64))
        case GpType18.Custom:
            custom = cast("CustomParameter", param).value
            return (
                bytes([custom["id"]])
                + write_compressed_uint(len(custom["data"]))
                + custom["data"]
            )
        case GpType18.Hashtable:
            return _write_hashtable(cast("HashtableParameter[Any, Any]", param))
        case GpType18.Dictionary:
            dictionary = cast("DictionaryParameter[Any, Any]", param)
            header, key_code, value_code = _dictionary_header(dictionary)
            return header + _write_dictionary_entries(dictionary, key_code, value_code)
        case GpType18.ObjectArray | GpType18.Array:
            elements = cast("Iterable[ParameterBase[Any]]", param.value)
            items = list(elements)
            return write_compressed_uint(len(items)) + b"".join(
                write_value(element) for element in items
            )
        case GpType18.ByteArray:
            data = (
                param.value
                if isinstance(param, Int8SliceParameter)
                else bytes(element.value for element in param.value)
            )
            return write_compressed_uint(len(data)) + data
    return _write_typed_array(param, code)


def _write_typed_array(param: ParameterBase[Any], code: int) -> bytes:
    elements = _array_elements(param)
    if code == GpType18.DictionaryArray:
        # The dictionary type comes before the count here (unlike other arrays).
        return _write_dictionary_array(
            cast("list[DictionaryParameter[Any, Any]]", elements)
        )
    out = bytearray(write_compressed_uint(len(elements)))
    match code:
        case GpType18.BooleanArray:
            out += _pack_bits(elements)
        case GpType18.CustomTypeArray:
            customs = cast("list[CustomParameter]", elements)
            out.append(customs[0].value["id"] if customs else 0)
            for custom in customs:
                data = custom.value["data"]
                out += write_compressed_uint(len(data)) + data
        case GpType18.HashtableArray:
            for table in elements:
                out += _write_hashtable(cast("HashtableParameter[Any, Any]", table))
        case _:
            element_code = GpType18(code & ~GpType18.Array)
            for element in elements:
                out += _write_body(element, element_code)
    return bytes(out)


def _write_dictionary_array(
    dictionaries: list[DictionaryParameter[Any, Any]],
) -> bytes:
    header, key_code, value_code = (
        _dictionary_header(dictionaries[0])
        if dictionaries
        else (bytes(2), GpType18.Unknown, GpType18.Unknown)
    )
    out = bytearray(header)
    out += write_compressed_uint(len(dictionaries))
    for dictionary in dictionaries:
        out += _write_dictionary_entries(dictionary, key_code, value_code)
    return bytes(out)


def _pack_bits(elements: list[ParameterBase[Any]]) -> bytes:
    """Pack booleans eight to a byte, the first in the lowest bit.

    Returns:
        The packed bytes.
    """
    out = bytearray((len(elements) + _BITS_PER_BYTE - 1) // _BITS_PER_BYTE)
    for index, element in enumerate(elements):
        if element.value:
            out[index // _BITS_PER_BYTE] |= 1 << (index % _BITS_PER_BYTE)
    return bytes(out)


def _array_elements(param: ParameterBase[Any]) -> list[ParameterBase[Any]]:
    if isinstance(param, StringSliceParameter):
        return [StringParameter(value) for value in param.value]
    if isinstance(param, Int32SliceParameter):
        return [Int32Parameter(value) for value in param.value]
    return list(cast("Iterable[ParameterBase[Any]]", param.value))


def _write_hashtable(table: HashtableParameter[Any, Any]) -> bytes:
    out = bytearray(write_compressed_uint(len(table)))
    for key, value in table.items():
        out += write_value(key) + write_value(value)
    return bytes(out)


_DICTIONARY_TYPED = {
    GpType18.Boolean,
    GpType18.Byte,
    GpType18.Short,
    GpType18.Float,
    GpType18.Double,
    GpType18.String,
    GpType18.CompressedInt,
    GpType18.CompressedLong,
    GpType18.Hashtable,
    GpType18.ByteArray,
}


def _dictionary_codes(dictionary: DictionaryParameter[Any, Any]) -> tuple[int, int]:
    def common(params: Iterable[ParameterBase[Any]]) -> int:
        codes = {type_code(param) for param in params}
        if len(codes) != 1:
            return GpType18.Unknown
        (code,) = codes
        return code if code in _DICTIONARY_TYPED else GpType18.Unknown

    return common(dictionary.keys()), common(dictionary.values())


def _dictionary_header(
    dictionary: DictionaryParameter[Any, Any],
) -> tuple[bytes, int, int]:
    """Type header plus key and value codes: as received, or worked out.

    Array values are declared ``object`` instead: the C++ SDK writes them in a
    form its own reader (and the .NET one) doesn't read back, so each value
    carries its full type instead.
    """
    if (header := dictionary.type_header) is not None:
        if header[1] == GpType18.Array:
            return bytes([header[0], GpType18.Unknown]), header[0], GpType18.Unknown
        return header, header[0], header[1]
    key_code, value_code = _dictionary_codes(dictionary)
    return bytes([key_code, value_code]), key_code, value_code


def _write_dictionary_entries(
    dictionary: DictionaryParameter[Any, Any], key_code: int, value_code: int
) -> bytes:
    out = bytearray(write_compressed_uint(len(dictionary)))
    for key, value in dictionary.items():
        out += (
            write_value(key)
            if key_code == GpType18.Unknown
            else _write_body(key, key_code)
        )
        out += (
            write_value(value)
            if value_code == GpType18.Unknown
            else _write_body(value, value_code)
        )
    return bytes(out)


# -- reading ------------------------------------------------------------------


def read_value(stream: BytesIO, code: int | None = None) -> ParameterBase[Any]:
    """Deserialize one value; reads the type code first unless given.

    Returns:
        The value as the matching ``*Parameter``.

    Raises:
        EOFError: The stream ended early.
        ValueError: Unknown type code.
    """
    if code is None:
        raw = stream.read(1)
        if not raw:
            msg = "Unexpected end of stream while reading type code."
            raise EOFError(msg)
        code = raw[0]
    if code >= GpType18.CustomTypeSlim:
        size = read_compressed_uint(stream)
        return CustomParameter({"id": code & 0x7F, "data": stream.read(size)})
    if (constant := _CONSTANTS.get(code)) is not None:
        return constant()
    reader = _READERS.get(code)
    if reader is None:
        if code & GpType18.Array:
            return _read_typed_array(stream, code)
        msg = f"Unknown Protocol 1.8 type code: {code}"
        raise ValueError(msg)
    return reader(stream)


_CONSTANTS: dict[int, Callable[[], ParameterBase[Any]]] = {
    GpType18.Unknown: NilParameter,
    GpType18.Null: NilParameter,
    GpType18.BooleanFalse: lambda: BooleanParameter(value=False),
    GpType18.BooleanTrue: lambda: BooleanParameter(value=True),
    GpType18.ByteZero: lambda: Int8Parameter(0),
    GpType18.ShortZero: lambda: Int16Parameter(0),
    GpType18.IntZero: lambda: Int32Parameter(0),
    GpType18.LongZero: lambda: Int64Parameter(0),
    GpType18.FloatZero: lambda: Float32Parameter(0.0),
    GpType18.DoubleZero: lambda: DoubleParameter(0.0),
}


def _read_custom(stream: BytesIO) -> CustomParameter:
    custom_id = _read(stream, "<B")
    size = read_compressed_uint(stream)
    return CustomParameter({"id": custom_id, "data": stream.read(size)})


def _read_dictionary(stream: BytesIO) -> DictionaryParameter[Any, Any]:
    header, key_code, value_code = _read_dictionary_type(stream)
    dictionary = _read_dictionary_entries(stream, key_code, value_code)
    dictionary.type_header = header
    return dictionary


def _read_dictionary_type(stream: BytesIO) -> tuple[bytes, int, int]:
    """Read a dictionary's type header; also return its raw bytes."""
    start = stream.tell()
    key_code, value_code = _read_dictionary_header(stream)
    return stream.getvalue()[start : stream.tell()], key_code, value_code


def _read_dictionary_header(stream: BytesIO) -> tuple[int, int]:
    key_code = _read(stream, "<B")
    value_code = _read(stream, "<B")
    # Nested container types are spelled out here, then again in each value.
    if value_code == GpType18.Dictionary:
        _read_dictionary_header(stream)
    elif value_code == GpType18.Array:
        _skip_array_type(stream)
    return key_code, value_code


def _skip_array_type(stream: BytesIO) -> None:
    code = _read(stream, "<B")
    while code == GpType18.Array:
        code = _read(stream, "<B")
    if code == GpType18.Dictionary:
        _read_dictionary_header(stream)


def _read_dictionary_entries(
    stream: BytesIO, key_code: int, value_code: int
) -> DictionaryParameter[Any, Any]:
    result: dict[ParameterBase[Any], ParameterBase[Any]] = {}
    for _ in range(read_compressed_uint(stream)):
        key = read_value(stream, _declared(key_code))
        result[key] = read_value(stream, _declared(value_code))
    return DictionaryParameter(cast("Any", result))


def _declared(code: int) -> int | None:
    """The code to read a dictionary entry with; None if it carries its own.

    The C++ SDK repeats an array value's full type per entry even when the
    header declares it.
    """
    return None if code in {GpType18.Unknown, GpType18.Array} else code


def _read_hashtable(stream: BytesIO) -> HashtableParameter[Any, Any]:
    result: dict[ParameterBase[Any], ParameterBase[Any]] = {}
    for _ in range(read_compressed_uint(stream)):
        key = read_value(stream)
        result[key] = read_value(stream)
    return HashtableParameter(cast("Any", result))


def _read_object_array(stream: BytesIO) -> ObjectSliceParameter:
    count = read_compressed_uint(stream)
    return ObjectSliceParameter([read_value(stream) for _ in range(count)])


def _read_array_of_arrays(stream: BytesIO) -> SliceParameter[Any]:
    count = read_compressed_uint(stream)
    return SliceParameter([read_value(stream) for _ in range(count)])


def _read_byte_array(stream: BytesIO) -> Int8SliceParameter:
    return Int8SliceParameter(stream.read(read_compressed_uint(stream)))


_READERS: dict[int, Callable[[BytesIO], ParameterBase[Any]]] = {
    GpType18.Boolean: lambda s: BooleanParameter(value=_read(s, "<B") != 0),
    GpType18.Byte: lambda s: Int8Parameter(_read(s, "<B")),
    GpType18.Short: lambda s: Int16Parameter(_read(s, "<h")),
    GpType18.Float: lambda s: Float32Parameter(_read(s, "<f")),
    GpType18.Double: lambda s: DoubleParameter(_read(s, "<d")),
    GpType18.String: lambda s: StringParameter(_read_string(s)),
    GpType18.CompressedInt: lambda s: Int32Parameter(
        _unzigzag(read_compressed_uint(s))
    ),
    GpType18.CompressedLong: lambda s: Int64Parameter(
        _unzigzag(read_compressed_uint(s))
    ),
    GpType18.Int1: lambda s: Int32Parameter(_read(s, "<B")),
    GpType18.Int1Negative: lambda s: Int32Parameter(-_read(s, "<B")),
    GpType18.Int2: lambda s: Int32Parameter(_read(s, "<H")),
    GpType18.Int2Negative: lambda s: Int32Parameter(-_read(s, "<H")),
    GpType18.Long1: lambda s: Int64Parameter(_read(s, "<B")),
    GpType18.Long1Negative: lambda s: Int64Parameter(-_read(s, "<B")),
    GpType18.Long2: lambda s: Int64Parameter(_read(s, "<H")),
    GpType18.Long2Negative: lambda s: Int64Parameter(-_read(s, "<H")),
    GpType18.Custom: _read_custom,
    GpType18.Dictionary: _read_dictionary,
    GpType18.Hashtable: _read_hashtable,
    GpType18.ObjectArray: _read_object_array,
    GpType18.Array: _read_array_of_arrays,
    GpType18.ByteArray: _read_byte_array,
}

# 1.8 array code -> element type of the equivalent 1.6 typed array.
_ELEMENT_TYPES = {code: element for element, code in _ARRAY_CODES.items()}


def _read_typed_array(stream: BytesIO, code: int) -> SliceParameter[Any]:
    if code == GpType18.DictionaryArray:
        # The dictionary type comes before the count here (unlike other arrays).
        header, key_code, value_code = _read_dictionary_type(stream)
        dictionaries = [
            _read_dictionary_entries(stream, key_code, value_code)
            for _ in range(read_compressed_uint(stream))
        ]
        for dictionary in dictionaries:
            dictionary.type_header = header
        return SliceParameter(
            cast("Any", dictionaries), element_type=_ELEMENT_TYPES.get(GpType18(code))
        )
    count = read_compressed_uint(stream)
    elements: list[ParameterBase[Any]] = []
    match code:
        case GpType18.BooleanArray:
            data = stream.read((count + _BITS_PER_BYTE - 1) // _BITS_PER_BYTE)
            elements = [
                BooleanParameter(value=bool(data[i // _BITS_PER_BYTE] >> (i % 8) & 1))
                for i in range(count)
            ]
        case GpType18.CustomTypeArray:
            custom_id = _read(stream, "<B")
            for _ in range(count):
                size = read_compressed_uint(stream)
                elements.append(
                    CustomParameter({"id": custom_id, "data": stream.read(size)})
                )
        case GpType18.HashtableArray:
            elements = [_read_hashtable(stream) for _ in range(count)]
        case _:
            if code not in _ELEMENT_TYPES:
                msg = f"Unknown Protocol 1.8 array type code: {code}"
                raise ValueError(msg)
            element_code = code & ~GpType18.Array
            elements = [read_value(stream, element_code) for _ in range(count)]
    element_type = _ELEMENT_TYPES.get(GpType18(code))
    return SliceParameter(cast("Any", elements), element_type=element_type)


# -- messages -----------------------------------------------------------------


def write_parameter_table(params: dict[int, ParameterBase[Any]]) -> bytes:
    """Serialize an operation's or event's parameters (one-byte count).

    Returns:
        The table.

    Raises:
        ValueError: More than 255 parameters.
    """
    if len(params) > _BYTE_MAX:
        msg = "Protocol 1.8 allows at most 255 parameters"
        raise ValueError(msg)
    out = bytearray([len(params)])
    for key, value in params.items():
        out.append(int(key))
        out += write_value(value)
    return bytes(out)


def read_parameter_table(stream: BytesIO) -> dict[int, ParameterBase[Any]]:
    """Deserialize a table written by :func:`write_parameter_table`.

    Returns:
        Parameters by key.
    """
    raw = stream.read(1)
    params: dict[int, ParameterBase[Any]] = {}
    for _ in range(raw[0] if raw else 0):
        key = _read(stream, "<B")
        params[key] = read_value(stream)
    return params


def write_response_header(return_code: int, debug: ParameterBase[Any]) -> bytes:
    """Serialize the return code and debug message of an operation response.

    Returns:
        The header.
    """
    return struct.pack("<h", return_code) + write_value(debug)


def read_response_header(stream: BytesIO) -> tuple[int, ParameterBase[Any]]:
    """Deserialize what :func:`write_response_header` wrote.

    Returns:
        Return code and debug message (``NilParameter`` when absent).
    """
    return_code = _read(stream, "<h")
    return return_code, read_value(stream)


__all__ = [
    "GpType18",
    "read_compressed_uint",
    "read_parameter_table",
    "read_response_header",
    "read_value",
    "type_code",
    "write_compressed_uint",
    "write_parameter_table",
    "write_response_header",
    "write_value",
]
