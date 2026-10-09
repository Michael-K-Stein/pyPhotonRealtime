from __future__ import annotations

import struct
from io import BytesIO
from typing import TYPE_CHECKING, Any

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.param.nil_param import NilParameter
from pyphotonrealtime.protocol.param.read_param import get_type_for_instance
from pyphotonrealtime.protocol.protocol18 import (
    write_parameter_table,
    write_response_header,
)
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader
    from pyphotonrealtime.protocol.param.base import ParameterBase
    from pyphotonrealtime.protocol.param.parameter_key import ParameterKey


def serialize_photon_payload(
    operation_code: OperationCode,
    params: dict[ParameterKey, ParameterBase[Any]],
    response_debug_data: tuple[int, ParameterBase[Any]] | None,
    header: PhotonDataPacketHeader,
    protocol: SerializationProtocol = SerializationProtocol.V16,
) -> bytes:
    """Serialize an operation code and its parameters into a binary payload."""
    if protocol == SerializationProtocol.V18:
        return _serialize_v18(operation_code, params, response_debug_data, header)
    stream = BytesIO()

    if operation_code == OperationCode.DiffieHellmanResponse:
        stream.write(b"\x00\x00\x00")

    if header.command != CommandCode.DisconnectMessage:
        stream.write(struct.pack(">B", operation_code))

    if response_debug_data is not None:
        stream.write(struct.pack(">h", response_debug_data[0]))
        response_debug_data_type_code = get_type_for_instance(response_debug_data[1])
        stream.write(struct.pack(">B", response_debug_data_type_code.value))
        stream.write(response_debug_data[1].serialize())

    stream.write(struct.pack(">h", len(params)))

    for param_key, param_obj in params.items():
        stream.write(struct.pack(">B", param_key))

        type_code = get_type_for_instance(param_obj)
        stream.write(struct.pack(">B", type_code.value))

        stream.write(param_obj.serialize())

    return stream.getvalue()


def _serialize_v18(
    operation_code: OperationCode,
    params: dict[ParameterKey, ParameterBase[Any]],
    response_debug_data: tuple[int, ParameterBase[Any]] | None,
    header: PhotonDataPacketHeader,
) -> bytes:
    out = bytearray()
    if header.command != CommandCode.DisconnectMessage:
        # The 1.6 key exchange answer smuggles its header into the op code.
        code = (
            OperationCode.DiffieHellmanRequest
            if operation_code == OperationCode.DiffieHellmanResponse
            else operation_code
        )
        out.append(code)
    if response_debug_data is not None:
        out += write_response_header(*response_debug_data)
    elif header.is_response() or header.command == CommandCode.DisconnectMessage:
        out += write_response_header(0, NilParameter())
    out += write_parameter_table({int(key): value for key, value in params.items()})
    return bytes(out)
