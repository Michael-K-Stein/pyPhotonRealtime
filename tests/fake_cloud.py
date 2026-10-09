"""In-process fake Photon servers for ``RealtimeClient`` tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast, override

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.packet.factory import PacketFactory
from pyphotonrealtime.protocol.packet.init import InitRequestPacket, InitResponsePacket
from pyphotonrealtime.protocol.packet.key_exchange import (
    InitEncryptionRequest,
    InitEncryptionResponse,
)
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.packet.packet_stream import PhotonStreamParser
from pyphotonrealtime.protocol.param.dict_param import DictionaryParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.protocol.photon_enc import generate_dh_keys
from pyphotonrealtime.transport import Transport

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyphotonrealtime.protocol.enum_lookups import CommandParams
    from pyphotonrealtime.protocol.event_code import EventCode
    from pyphotonrealtime.protocol.packet.base import PhotonPacket

    type Handler = Callable[[CommandParams], tuple[int, CommandParams]]

APP_ID = "6f869876-bfbc-491e-8fff-4210c966f145"
NAME_SERVER = "ns.example:4533"
MASTER_SERVER = "ms.example:4530"
GAME_SERVER = "gs.example:4531"
SECRET = bytes(range(32))


class FakeServer:
    """A Photon server answering Init, key exchange and operations.

    Authentication is built in; other operations are answered by ``handlers``
    (operation code -> function of the request parameters).
    """

    def __init__(self, address: str) -> None:
        self.address = address
        self.parser = PhotonStreamParser()
        self.aes_key: bytes | None = None
        self.operations: list[tuple[int, CommandParams, bool]] = []
        self.auth_return_code = 0
        self.regions: dict[str, str] = {}
        self.handlers: dict[int, Handler] = {}

    def reset(self) -> None:
        self.parser = PhotonStreamParser()
        self.aes_key = None

    def handle(self, data: bytes) -> bytes:
        self.parser.feed(data)
        out = b""
        for packet in self.parser.parse(aes_key=self.aes_key):
            for reply in self._replies(packet):
                out += reply.serialize()
        return out

    def ops(self, code: int) -> list[CommandParams]:
        """Parameters of every received operation with ``code``."""
        return [params for op, params, _ in self.operations if op == code]

    def _replies(self, packet: PhotonPacket) -> list[PhotonPacket]:
        if isinstance(packet, InitRequestPacket):
            return [InitResponsePacket()]
        if isinstance(packet, InitEncryptionRequest):
            server_public, self.aes_key = generate_dh_keys(packet.get_public_key())
            return [InitEncryptionResponse(public_key=server_public)]
        if isinstance(packet, PhotonOperationPacket):
            payload = packet.get_payload()
            encrypted = packet.get_header().is_encrypted()
            request = dict(payload.params)
            self.operations.append((int(payload.operation_code), request, encrypted))
            return_code, params = self.answer(int(payload.operation_code), request)
            response = PacketFactory.operation(
                CommandCode.EncryptedOperationResponse
                if encrypted
                else CommandCode.OperationResponse,
                operation=payload.operation_code,
                params=params,
                return_code=return_code,
                error_message="nope" if return_code else None,
            )
            if self.aes_key is not None:
                response.set_aes_key(self.aes_key)
            return [response]
        return []

    def answer(
        self, operation: int, request: CommandParams
    ) -> tuple[int, CommandParams]:
        if (handler := self.handlers.get(operation)) is not None:
            return handler(request)
        if operation == OperationCode.GetRegions:
            return 0, {
                ParameterKey.Region: SliceParameter(
                    [StringParameter(code) for code in self.regions]
                ),
                ParameterKey.Address: SliceParameter(
                    [StringParameter(addr) for addr in self.regions.values()]
                ),
            }
        if operation not in {OperationCode.Authenticate, OperationCode.AuthOnce}:
            return 0, {}
        if self.auth_return_code:
            return self.auth_return_code, {}
        params: CommandParams = {
            ParameterKey.Token: StringParameter("token-1"),
            ParameterKey.UserId: StringParameter("user-1"),
        }
        if self.address == NAME_SERVER:
            params[ParameterKey.Address] = StringParameter(MASTER_SERVER)
            params[ParameterKey.Cluster] = StringParameter("default")
        if operation == OperationCode.AuthOnce:
            params[ParameterKey.EncryptionData] = DictionaryParameter(
                cast(
                    "Any",
                    {
                        Int8Parameter(0): Int8SliceParameter(b"\x00"),
                        Int8Parameter(1): Int8SliceParameter(SECRET),
                    },
                )
            )
        return 0, params


class FakeCloud(Transport):
    """Routes the client's connection to the fake server at that address."""

    def __init__(self, *servers: FakeServer) -> None:
        self.servers = {server.address: server for server in servers}
        self.current: FakeServer | None = None
        self.inbox = b""
        self.connections: list[str] = []

    def push_event(self, code: int, params: CommandParams) -> None:
        """Deliver an event from the current server on the next receive."""
        event = PacketFactory.event(cast("EventCode", code), params)
        self.inbox += event.serialize()

    @override
    def connect(self, host: str, port: int, timeout: float) -> None:
        address = f"{host}:{port}"
        if address not in self.servers:
            raise ConnectionRefusedError
        self.connections.append(address)
        self.current = self.servers[address]
        self.current.reset()
        self.inbox = b""

    @override
    def send(self, data: bytes) -> None:
        assert self.current is not None
        self.inbox += self.current.handle(data)

    @override
    def receive(self) -> bytes:
        data, self.inbox = self.inbox, b""
        return data

    @override
    def close(self) -> None:
        self.current = None

    @property
    @override
    def connected(self) -> bool:
        return self.current is not None
