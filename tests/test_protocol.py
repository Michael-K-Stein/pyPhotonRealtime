"""Round-trip tests for the wire layer inherited from prison-architect-server."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Any

import pytest

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.consts import MSG_MAGIC
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.packet.factory import PacketFactory
from pyphotonrealtime.protocol.packet.init import InitRequestPacket
from pyphotonrealtime.protocol.packet.keep_alive import PhotonKeepAliveRequest
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.packet.packet_stream import PhotonStreamParser
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.hashtable_param import HashtableParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.read_param import (
    get_type_for_instance,
    read_parameter,
)
from pyphotonrealtime.protocol.param.string_param import StringParameter

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.param.base import ParameterBase

APP_ID = "6f869876bfbc491e8fff4210c966f145"


@pytest.mark.parametrize(
    "param",
    [
        BooleanParameter(value=True),
        Int32Parameter(-123456),
        StringParameter("hello photon"),
        HashtableParameter({StringParameter("k"): Int8Parameter(7)}),
    ],
)
def test_parameter_round_trip(param: ParameterBase[Any]) -> None:
    type_code = get_type_for_instance(param)
    decoded = read_parameter(BytesIO(param.serialize()), type_code.value)
    assert decoded == param


def test_hashtable_keys_are_typed() -> None:
    # Int8(4) and Int32(4) are different keys on the wire, so they must not collide.
    table = HashtableParameter(
        {
            Int8Parameter(4): StringParameter("Bar"),
            Int32Parameter(4): StringParameter("Conflict"),
        }
    )
    assert table[Int8Parameter(4)] == StringParameter("Bar")
    assert table[Int32Parameter(4)] == StringParameter("Conflict")


def test_header_carries_magic_byte() -> None:
    raw = InitRequestPacket(app_id=APP_ID).serialize()
    # format byte, 4-byte length, 2-byte peer id, then the magic byte.
    assert raw[7] == MSG_MAGIC


def test_init_request_round_trip() -> None:
    parser = PhotonStreamParser()
    parser.feed(InitRequestPacket(app_id=APP_ID, sdk_version="4.1.11.0").serialize())
    (packet,) = parser.parse()
    assert isinstance(packet, InitRequestPacket)
    assert packet.app_id == APP_ID
    assert packet.sdk_version == "4.1.11.0"


def test_operation_round_trip() -> None:
    sent = PacketFactory.operation(
        CommandCode.Operation,
        operation=OperationCode.JoinGame,
        params={ParameterKey.GameId: StringParameter("room-1")},
    )
    parser = PhotonStreamParser()
    parser.feed(sent.serialize())
    (packet,) = parser.parse()
    assert isinstance(packet, PhotonOperationPacket)
    assert packet.get_payload().operation_code == OperationCode.JoinGame
    assert packet.get_payload().params == {
        ParameterKey.GameId: StringParameter("room-1")
    }


def test_parser_waits_for_complete_packets() -> None:
    raw = PhotonKeepAliveRequest(client_time=1234).serialize()
    raw += InitRequestPacket(app_id=APP_ID).serialize()
    parser = PhotonStreamParser()

    parser.feed(raw[:3])  # Not even a whole keep-alive yet.
    assert list(parser.parse()) == []

    parser.feed(raw[3:-1])  # Keep-alive complete, init still missing a byte.
    (keep_alive,) = parser.parse()
    assert isinstance(keep_alive, PhotonKeepAliveRequest)
    assert keep_alive.get_client_time() == 1234

    parser.feed(raw[-1:])
    (init,) = parser.parse()
    assert isinstance(init, InitRequestPacket)
    assert not parser.buffer
