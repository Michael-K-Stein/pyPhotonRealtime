"""``PhotonPeer`` against a fake in-process server.

The fake server reuses the server-side half of the protocol package (inherited
from prison-architect-server): it parses client packets and answers the way a
Photon server does.
"""

from __future__ import annotations

import socket
import threading
from typing import TYPE_CHECKING, Any, override

import pytest

from pyphotonrealtime.peer import (
    EventData,
    OperationResponse,
    PeerState,
    PhotonPeer,
    StatusCode,
)
from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.packet.disconnect import DisconnectMessagePacket
from pyphotonrealtime.protocol.packet.factory import PacketFactory
from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader
from pyphotonrealtime.protocol.packet.init import InitRequestPacket, InitResponsePacket
from pyphotonrealtime.protocol.packet.keep_alive import (
    PhotonKeepAliveRequest,
    PhotonKeepAliveResponse,
)
from pyphotonrealtime.protocol.packet.key_exchange import (
    InitEncryptionRequest,
    InitEncryptionResponse,
)
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.packet.operation_payload import PhotonPacketPayload
from pyphotonrealtime.protocol.packet.packet_stream import PhotonStreamParser
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.protocol.photon_enc import generate_dh_keys
from pyphotonrealtime.transport import TcpTransport, Transport

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.event_code import EventCode
    from pyphotonrealtime.protocol.packet.base import PhotonPacket
    from pyphotonrealtime.protocol.param.base import ParameterBase

APP_ID = "6f869876-bfbc-491e-8fff-4210c966f145"
ECHO_EVENT = 100


class FakeServer:
    """Answers client packets like a Photon server; transport-agnostic."""

    def __init__(self) -> None:
        self.parser = PhotonStreamParser()
        self.aes_key: bytes | None = None
        self.received: list[PhotonPacket] = []
        self.answer_keep_alives = True

    def handle(self, data: bytes) -> bytes:
        self.parser.feed(data)
        out = b""
        for packet in self.parser.parse(aes_key=self.aes_key):
            self.received.append(packet)
            for reply in self._replies(packet):
                out += reply.serialize()
        return out

    def _replies(self, packet: PhotonPacket) -> list[PhotonPacket]:
        if isinstance(packet, InitRequestPacket):
            return [InitResponsePacket()]
        if isinstance(packet, PhotonKeepAliveRequest):
            if not self.answer_keep_alives:
                return []
            return [PhotonKeepAliveResponse(1, packet.get_client_time())]
        if isinstance(packet, InitEncryptionRequest):
            server_public, self.aes_key = generate_dh_keys(packet.get_public_key())
            return [InitEncryptionResponse(public_key=server_public)]
        if isinstance(packet, PhotonOperationPacket):
            return self._echo(packet)
        return []

    def _echo(self, packet: PhotonOperationPacket) -> list[PhotonPacket]:
        """Answer an operation with its own parameters, plus an event."""
        payload = packet.get_payload()
        encrypted = packet.get_header().is_encrypted()
        response = PacketFactory.operation(
            CommandCode.EncryptedOperationResponse
            if encrypted
            else CommandCode.OperationResponse,
            operation=payload.operation_code,
            params=dict(payload.params),
            return_code=0,
            error_message="ok",
        )
        event = PacketFactory.event(
            event=cast_event(ECHO_EVENT), params=dict(payload.params)
        )
        if self.aes_key is not None:
            response.set_aes_key(self.aes_key)
        return [response, event]

    @staticmethod
    def disconnect_message() -> PhotonPacket:
        header = PhotonDataPacketHeader(command_code=CommandCode.DisconnectMessage)
        return DisconnectMessagePacket(
            header=header,
            payload=PhotonPacketPayload(
                operation_code=OperationCode.DiffieHellmanRequest,
                params={},
                header=header,
                response_debug_data=(0, StringParameter("bye")),
            ),
        )


def cast_event(code: int) -> EventCode:
    return code  # type: ignore[return-value]


class FakeTransport(Transport):
    """Delivers bytes straight to a ``FakeServer``; replies come back on receive."""

    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.inbox = b""
        self.is_open = False
        self.refuse = False

    @override
    def connect(self, host: str, port: int, timeout: float) -> None:
        if self.refuse:
            raise ConnectionRefusedError
        self.is_open = True

    @override
    def send(self, data: bytes) -> None:
        self.inbox += self.server.handle(data)

    @override
    def receive(self) -> bytes:
        data, self.inbox = self.inbox, b""
        return data

    @override
    def close(self) -> None:
        self.is_open = False

    @property
    @override
    def connected(self) -> bool:
        return self.is_open


class Recorder:
    def __init__(self) -> None:
        self.statuses: list[StatusCode] = []
        self.responses: list[OperationResponse] = []
        self.events: list[EventData] = []

    def on_status_changed(self, status: StatusCode) -> None:
        self.statuses.append(status)

    def on_operation_response(self, response: OperationResponse) -> None:
        self.responses.append(response)

    def on_event(self, event: EventData) -> None:
        self.events.append(event)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(PhotonPeer, "_now", staticmethod(clock))
    return clock


@pytest.fixture
def listener() -> Recorder:
    return Recorder()


@pytest.fixture
def peer(server: FakeServer, listener: Recorder, clock: Clock) -> PhotonPeer:
    del clock  # Only needed so the peer is created with the fake clock.
    return PhotonPeer(listener, FakeTransport(server))


def connect(peer: PhotonPeer, listener: Recorder) -> None:
    assert peer.connect("ns.example:4533", APP_ID)
    peer.service()  # Sends Init.
    peer.service()  # Dispatches InitResponse.
    assert listener.statuses == [StatusCode.Connect]
    assert peer.state == PeerState.Connected


PARAMS: dict[int, ParameterBase[Any]] = {
    ParameterKey.UserId: StringParameter("player-1")
}


def test_connect_sends_init_with_app_id(
    peer: PhotonPeer, listener: Recorder, server: FakeServer
) -> None:
    connect(peer, listener)
    (init,) = server.received
    assert isinstance(init, InitRequestPacket)
    assert init.app_id == APP_ID.replace("-", "")


def test_connect_failure_reports_exception_on_connect(
    peer: PhotonPeer, listener: Recorder
) -> None:
    assert isinstance(peer.transport, FakeTransport)
    peer.transport.refuse = True
    assert not peer.connect("ns.example:4533", APP_ID)
    peer.service()
    assert listener.statuses == [StatusCode.ExceptionOnConnect]
    assert peer.state == PeerState.Disconnected


def test_connect_rejects_address_without_port(peer: PhotonPeer) -> None:
    with pytest.raises(ValueError, match="host:port"):
        peer.connect("ns.example", APP_ID)


def test_operation_round_trip(peer: PhotonPeer, listener: Recorder) -> None:
    assert not peer.send_operation(OperationCode.Authenticate, PARAMS)  # Too early.
    connect(peer, listener)

    assert peer.send_operation(OperationCode.Authenticate, PARAMS)
    peer.service()
    peer.service()

    assert listener.responses == [
        OperationResponse(
            operation_code=OperationCode.Authenticate,
            return_code=0,
            debug_message="ok",
            parameters=PARAMS,
        )
    ]
    assert listener.events == [EventData(code=ECHO_EVENT, parameters=PARAMS)]
    assert peer.traffic_stats.packets_out == 2  # Init + operation.
    assert peer.traffic_stats.packets_in == 3  # InitResponse, response, event.
    assert peer.traffic_stats.bytes_in > 0
    assert peer.traffic_stats.bytes_out > 0


def test_encrypted_operation(
    peer: PhotonPeer, listener: Recorder, server: FakeServer
) -> None:
    connect(peer, listener)
    assert not peer.send_operation(OperationCode.Authenticate, PARAMS, encrypt=True)

    assert peer.establish_encryption()
    assert not peer.establish_encryption()  # Already in progress.
    peer.service()
    peer.service()
    assert listener.statuses[-1] == StatusCode.EncryptionEstablished
    assert peer.is_encryption_available
    assert peer._aes_key == server.aes_key

    assert peer.send_operation(OperationCode.Authenticate, PARAMS, encrypt=True)
    peer.service()
    peer.service()
    sent = server.received[-1]
    assert isinstance(sent, PhotonOperationPacket)
    assert sent.get_header().get_command_code() == CommandCode.EncryptedOperation
    assert listener.responses[-1].parameters == PARAMS


def test_keep_alive_when_idle_and_round_trip_time(
    peer: PhotonPeer, listener: Recorder, server: FakeServer, clock: Clock
) -> None:
    connect(peer, listener)
    peer.service()
    assert not any(isinstance(p, PhotonKeepAliveRequest) for p in server.received)

    clock.now += peer.keep_alive_interval
    peer.service()  # Sends the ping; the fake answers instantly.
    clock.now += 0.125
    peer.service()  # Dispatches the pong 125 ms later.
    assert isinstance(server.received[-1], PhotonKeepAliveRequest)
    assert peer.last_round_trip_time == 125
    assert peer.round_trip_time == 125.0

    clock.now += peer.keep_alive_interval
    peer.service()
    clock.now += 0.25
    peer.service()
    assert peer.last_round_trip_time == 250
    assert peer.round_trip_time == 140.625  # 125 + (250 - 125) / 8
    assert peer.round_trip_time_variance == 31.25  # |125| / 4


def test_timeout_disconnect(
    peer: PhotonPeer, listener: Recorder, server: FakeServer, clock: Clock
) -> None:
    connect(peer, listener)
    server.answer_keep_alives = False
    for _ in range(int(peer.disconnect_timeout) + 2):
        clock.now += 1
        peer.service()
    assert listener.statuses[-1] == StatusCode.TimeoutDisconnect
    assert peer.state == PeerState.Disconnected
    assert not peer.transport.connected


def test_disconnect_by_server(peer: PhotonPeer, listener: Recorder) -> None:
    connect(peer, listener)
    assert isinstance(peer.transport, FakeTransport)
    peer.transport.inbox += FakeServer.disconnect_message().serialize()
    peer.service()
    assert listener.statuses[-1] == StatusCode.DisconnectByServer
    assert peer.state == PeerState.Disconnected


def test_connection_closed_by_server(peer: PhotonPeer, listener: Recorder) -> None:
    connect(peer, listener)
    peer.transport.close()
    peer.service()
    assert listener.statuses[-1] == StatusCode.DisconnectByServer


def test_client_disconnect(peer: PhotonPeer, listener: Recorder) -> None:
    connect(peer, listener)
    peer.disconnect()
    peer.disconnect()  # Idempotent.
    peer.service()
    assert listener.statuses == [StatusCode.Connect, StatusCode.Disconnect]
    assert not peer.transport.connected
    assert not peer.send_operation(OperationCode.Authenticate, PARAMS)


def test_garbage_from_server_is_an_exception(
    peer: PhotonPeer, listener: Recorder
) -> None:
    connect(peer, listener)
    assert isinstance(peer.transport, FakeTransport)
    peer.transport.inbox += b"\x42garbage"
    peer.service()
    assert listener.statuses[-1] == StatusCode.Exception
    assert peer.state == PeerState.Disconnected


def test_over_loopback_tcp() -> None:
    """The real ``TcpTransport`` against a socket-backed fake server."""
    fake = FakeServer()
    listening = socket.create_server(("127.0.0.1", 0))
    port = listening.getsockname()[1]

    def serve() -> None:
        conn, _ = listening.accept()
        with conn:
            while data := conn.recv(4096):
                conn.sendall(fake.handle(data))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    listener = Recorder()
    peer = PhotonPeer(listener)
    assert peer.connect(f"127.0.0.1:{port}", APP_ID)
    # Bigger than the socket buffers, so the transport must cope with partial writes.
    big: dict[int, ParameterBase[Any]] = {
        ParameterKey.Data: Int8SliceParameter(bytes(range(256)) * 4096)
    }
    sent = False
    deadline = 500
    while not listener.responses and deadline:
        if peer.state == PeerState.Connected and not sent:
            sent = peer.send_operation(OperationCode.RaiseEvent, big)
        peer.service()
        thread.join(0.01)
        deadline -= 1
    peer.disconnect()
    listening.close()

    assert listener.statuses[0] == StatusCode.Connect
    assert listener.responses[0].parameters == big
    assert isinstance(peer.transport, TcpTransport)
