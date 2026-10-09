"""UDP and WebSocket transports against local fake servers, plus peer support.

The fake servers unwrap the transport's framing and hand Photon messages, in
TCP framing, to ``test_peer.FakeServer``, which answers like a Photon server.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading
import time
from io import BytesIO
from typing import TYPE_CHECKING, Any, ClassVar, cast, override

import pytest

from pyphotonrealtime import AppSettings, RealtimeClient
from pyphotonrealtime.peer import PeerState, PhotonPeer, StatusCode, split_address
from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.packet.factory import PacketFactory
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.protocol.protocol18 import read_parameter_table
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol
from pyphotonrealtime.transport import (
    ConnectionProtocol,
    SecureWebSocketTransport,
    TcpTransport,
    UdpTransport,
    WebSocketTransport,
    create_transport,
)
from pyphotonrealtime.transport.udp import CommandType
from test_peer import APP_ID, ECHO_EVENT, FakeServer, FakeTransport, Recorder

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyphotonrealtime.protocol.enum_lookups import CommandParams
    from pyphotonrealtime.protocol.operation_code import OperationCode

TIMEOUT = 5.0
TCP_HEADER = struct.Struct(">BIBB")


def wrap_tcp(message: bytes, channel: int = 0, *, reliable: bool = True) -> bytes:
    return (
        TCP_HEADER.pack(0xFB, len(message) + TCP_HEADER.size, channel, reliable)
        + message
    )


def split_tcp(data: bytes) -> list[bytes]:
    messages = []
    while data:
        _, length, _, _ = TCP_HEADER.unpack_from(data)
        messages.append(data[TCP_HEADER.size : length])
        data = data[length:]
    return messages


def service_until(
    peer: PhotonPeer, condition: Callable[[], bool], *pumps: Callable[[], None]
) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out in {peer.state}")
        peer.service()
        for pump in pumps:
            pump()
        time.sleep(0.001)


def test_split_address() -> None:
    assert split_address("ns.example:4533") == ("ns.example", 4533, "")
    assert split_address("wss://gs.example:443/Master") == (
        "gs.example",
        443,
        "/Master",
    )
    assert split_address("[::1]:5055") == ("::1", 5055, "")
    with pytest.raises(ValueError, match="host:port"):
        split_address("wss://no-port/Master")


def test_create_transport() -> None:
    for protocol in ConnectionProtocol:
        assert create_transport(protocol).protocol == protocol


# -- UDP ------------------------------------------------------------------------

PACKET = struct.Struct(">HBBII")
COMMAND = struct.Struct(">BBBBII")
FRAGMENT = struct.Struct(">IIIII")


class FakeEnetServer:
    """Speaks Photon's UDP protocol on localhost; pumped from the test thread."""

    PEER_ID = 0x1234
    FRAGMENT_SIZE = 300

    def __init__(self, backend: FakeServer) -> None:
        self.backend = backend
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.setblocking(False)  # noqa: FBT003
        self.address = f"127.0.0.1:{self.sock.getsockname()[1]}"
        self.client: Any = None
        self.challenge = 0
        self.out_seq: dict[int, int] = {}
        self.in_seq: dict[int, int] = {}
        self.pending: dict[tuple[int, int], tuple[int, bytes]] = {}
        self.fragments: dict[int, list[bytes]] = {}
        self.received: list[tuple[int, bool, bytes]] = []
        """Messages from the client: (channel, reliable, message)."""
        self.commands: list[int] = []
        self.drop_incoming = 0
        self.reverse = False
        self.verify = True

    def close(self) -> None:
        self.sock.close()

    def poll(self) -> None:
        while True:
            try:
                datagram, self.client = self.sock.recvfrom(65536)
            except BlockingIOError:
                return
            if self.drop_incoming:
                self.drop_incoming -= 1
                continue
            self.handle(datagram)

    def handle(self, datagram: bytes) -> None:
        _, _, count, sent_time, self.challenge = PACKET.unpack_from(datagram)
        offset = PACKET.size
        for _ in range(count):
            kind, channel, flags, _, length, seq = COMMAND.unpack_from(datagram, offset)
            payload = datagram[offset + COMMAND.size : offset + length]
            offset += length
            self.commands.append(kind)
            if flags & 1:
                ack = struct.pack(">II", seq, sent_time)
                self.send([self.command(CommandType.Ack, channel, 0, ack, seq)])
            if kind == CommandType.Connect and self.verify:
                verify = struct.pack(">H", self.PEER_ID) + bytes(30)
                self.send([self.reliable(CommandType.VerifyConnect, 0xFF, verify)])
            elif kind in {CommandType.SendReliable, CommandType.SendFragment}:
                self.pending[channel, seq] = (kind, payload)
                self.deliver_in_order(channel)
            elif kind == CommandType.SendUnreliable:
                self.on_message(channel, payload[4:], reliable=False)

    def deliver_in_order(self, channel: int) -> None:
        while (
            ready := self.pending.pop((channel, self.in_seq.get(channel, 0) + 1), None)
        ) is not None:
            self.in_seq[channel] = self.in_seq.get(channel, 0) + 1
            kind, payload = ready
            if kind == CommandType.SendReliable:
                self.on_message(channel, payload, reliable=True)
                continue
            start, count, number, total, _ = FRAGMENT.unpack_from(payload)
            chunks = self.fragments.setdefault(start, [b""] * count)
            chunks[number] = payload[FRAGMENT.size :]
            if all(chunks):
                del self.fragments[start]
                message = b"".join(chunks)
                assert len(message) == total
                self.on_message(channel, message, reliable=True)

    def on_message(self, channel: int, message: bytes, *, reliable: bool) -> None:
        self.received.append((channel, reliable, message))
        reply = self.backend.handle(wrap_tcp(message, channel, reliable=reliable))
        for answer in split_tcp(reply):
            self.send_message(answer)

    def send_message(self, message: bytes, channel: int = 0) -> None:
        if len(message) <= self.FRAGMENT_SIZE:
            commands = [self.reliable(CommandType.SendReliable, channel, message)]
        else:
            size = self.FRAGMENT_SIZE
            count = (len(message) + size - 1) // size
            start = self.out_seq.get(channel, 0) + 1
            commands = [
                self.reliable(
                    CommandType.SendFragment,
                    channel,
                    FRAGMENT.pack(start, count, n, len(message), n * size)
                    + message[n * size : (n + 1) * size],
                )
                for n in range(count)
            ]
        self.send(commands)

    def reliable(self, kind: int, channel: int, payload: bytes) -> bytes:
        self.out_seq[channel] = self.out_seq.get(channel, 0) + 1
        return self.command(kind, channel, 1, payload, self.out_seq[channel])

    @staticmethod
    def command(kind: int, channel: int, flags: int, payload: bytes, seq: int) -> bytes:
        return (
            COMMAND.pack(kind, channel, flags, 4, COMMAND.size + len(payload), seq)
            + payload
        )

    def send(self, commands: list[bytes], challenge: int | None = None) -> None:
        if self.reverse:
            commands.reverse()
        for command in commands:  # One datagram each, so reversing reorders them.
            header = PACKET.pack(
                self.PEER_ID,
                0,
                1,
                0,
                self.challenge if challenge is None else challenge,
            )
            self.sock.sendto(header + command, self.client)


@pytest.fixture
def enet() -> Any:  # noqa: ANN401
    server = FakeEnetServer(FakeServer())
    yield server
    server.close()


def udp_peer(enet: FakeEnetServer) -> tuple[PhotonPeer, Recorder]:
    recorder = Recorder()
    peer = PhotonPeer(recorder, UdpTransport(), keep_alive_interval=60)
    assert peer.connect(enet.address, APP_ID)
    service_until(peer, lambda: peer.state == PeerState.Connected, enet.poll)
    return peer, recorder


def test_udp_connects_and_exchanges_operations(enet: FakeEnetServer) -> None:
    peer, recorder = udp_peer(enet)
    assert enet.commands[:2] == [CommandType.Connect, CommandType.Ack]
    # The init request waited for VERIFY_CONNECT; afterwards the peer id is used.
    assert enet.received[0][2][:2] == b"\xf3\x00"
    assert cast("UdpTransport", peer.transport)._peer_id == FakeEnetServer.PEER_ID

    assert peer.send_operation(1, {2: StringParameter("hi")})
    service_until(peer, lambda: bool(recorder.events), enet.poll)
    assert recorder.responses[0].parameters == {2: StringParameter("hi")}
    assert recorder.events[0].code == ECHO_EVENT

    peer.disconnect()
    enet.poll()
    assert enet.commands[-1] == CommandType.Disconnect


def test_udp_fragments_large_messages_both_ways(enet: FakeEnetServer) -> None:
    peer, recorder = udp_peer(enet)
    blob = bytes(range(256)) * 40  # Over 10 KB: many fragments each way.
    assert peer.send_operation(1, {2: Int8SliceParameter(blob)})
    service_until(peer, lambda: bool(recorder.events), enet.poll)
    assert CommandType.SendFragment in enet.commands
    assert recorder.responses[0].parameters[2] == Int8SliceParameter(blob)
    assert recorder.events[0].parameters[2] == Int8SliceParameter(blob)


def test_udp_delivers_reliable_commands_in_order(enet: FakeEnetServer) -> None:
    peer, recorder = udp_peer(enet)
    enet.reverse = True
    for n in range(3):
        assert peer.send_operation(1, {2: Int32Parameter(n)})
    service_until(peer, lambda: len(recorder.events) == 3, enet.poll)
    assert [r.parameters[2] for r in recorder.responses] == [0, 1, 2]
    blob = bytes(2000)
    assert peer.send_operation(1, {2: Int8SliceParameter(blob)})
    service_until(peer, lambda: len(recorder.events) == 4, enet.poll)
    assert recorder.events[-1].parameters[2] == Int8SliceParameter(blob)


def test_udp_resends_until_acked(enet: FakeEnetServer) -> None:
    peer, recorder = udp_peer(enet)
    transport = cast("UdpTransport", peer.transport)
    transport._round_trip_time = 0.001  # Resend after the minimum timeout.
    enet.drop_incoming = 2
    assert peer.send_operation(1, {2: Int32Parameter(5)})
    service_until(peer, lambda: bool(recorder.responses), enet.poll)
    assert recorder.responses[0].parameters[2] == 5
    assert [m for m in enet.received if m[2][1] == CommandCode.Operation] == [
        enet.received[-1]
    ]  # Delivered exactly once.


def test_udp_gives_up_when_nothing_is_acked(enet: FakeEnetServer) -> None:
    recorder = Recorder()
    transport = UdpTransport()
    transport.MAX_RESEND_TIMEOUT = 0.01
    peer = PhotonPeer(recorder, transport)
    enet.verify = False
    enet.drop_incoming = 1000
    assert peer.connect(enet.address, APP_ID)
    service_until(peer, lambda: peer.state == PeerState.Disconnected, enet.poll)
    assert recorder.statuses == [StatusCode.DisconnectByServer]


def test_udp_unreliable_and_channels(enet: FakeEnetServer) -> None:
    peer, recorder = udp_peer(enet)
    assert peer.send_operation(1, {2: Int32Parameter(1)}, reliable=False, channel=1)
    service_until(peer, lambda: bool(recorder.responses), enet.poll)
    channel, reliable, _ = enet.received[-1]
    assert (channel, reliable) == (1, False)


def test_udp_ping_measures_round_trip_time(enet: FakeEnetServer) -> None:
    peer, _ = udp_peer(enet)
    peer.keep_alive_interval = 0
    service_until(peer, lambda: peer.last_round_trip_time is not None, enet.poll)
    assert CommandType.Ping in enet.commands


def test_udp_ignores_wrong_challenge_and_obeys_disconnect(
    enet: FakeEnetServer,
) -> None:
    peer, recorder = udp_peer(enet)
    bye = enet.reliable(CommandType.Disconnect, 0xFF, b"")
    enet.send([bye], challenge=enet.challenge ^ 1)
    for _ in range(20):
        peer.service()
    assert peer.state == PeerState.Connected
    enet.send([bye])
    service_until(peer, lambda: peer.state == PeerState.Disconnected, enet.poll)
    assert recorder.statuses[-1] == StatusCode.DisconnectByServer


# -- WebSockets -----------------------------------------------------------------

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FakeWebSocketServer(threading.Thread):
    """Accepts one WebSocket client and answers it through a ``FakeServer``."""

    def __init__(
        self, backend: FakeServer, extra_frames: list[bytes] | None = None
    ) -> None:
        super().__init__(daemon=True)
        self.backend = backend
        backend.parser.protocol = SerializationProtocol.V18  # No init request.
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.address = f"ws://127.0.0.1:{self.listener.getsockname()[1]}/Master"
        self.request_line = ""
        self.headers: dict[str, str] = {}
        self.messages: list[bytes] = []
        self.closed = threading.Event()
        self.extra_frames = extra_frames or []
        """Raw frames to send right after the upgrade."""
        self.start()

    def run(self) -> None:
        conn, _ = self.listener.accept()
        with conn:
            conn.settimeout(TIMEOUT)
            buffer = b""
            while b"\r\n\r\n" not in buffer:
                buffer += conn.recv(4096)
            head, _, buffer = buffer.partition(b"\r\n\r\n")
            self.request_line, *lines = head.decode().split("\r\n")
            self.headers = {
                k.strip().lower(): v.strip()
                for k, _, v in (line.partition(":") for line in lines)
            }
            accept = base64.b64encode(
                hashlib.sha1(  # noqa: S324 - mandated by RFC 6455
                    self.headers["sec-websocket-key"].encode() + WS_GUID
                ).digest()
            ).decode()
            conn.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                    f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode()
                + server_frame(0x2, b"\xf3\x01\x00")  # Init response.
                + b"".join(self.extra_frames)
            )
            self.serve(conn, buffer)
        self.closed.set()

    def serve(self, conn: socket.socket, buffer: bytes) -> None:
        while True:
            frame = read_client_frame(buffer)
            if frame is None:
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                continue
            opcode, payload, buffer = frame
            if opcode == 0x8:
                return
            if opcode != 0x2:
                self.messages.append(bytes([opcode]) + payload)
                continue
            self.messages.append(payload)
            for reply in self.answer(payload):
                conn.sendall(server_frame(0x2, reply))

    def answer(self, message: bytes) -> list[bytes]:
        if message[1] == CommandCode.InternalOperationRequest and message[2] == 1:
            ping = PacketFactory.operation(
                cast("Any", CommandCode.InternalOperationResponse),
                operation=cast("OperationCode", 1),
                params=cast("CommandParams", {1: Int32Parameter(0)}),
                return_code=0,
                protocol=SerializationProtocol.V18,
            ).get_payload()
            ping.params = cast(
                "CommandParams", {1: _client_time(message), 2: Int32Parameter(9)}
            )
            return [b"\xf3\x07" + ping.serialize()]
        return split_tcp(self.backend.handle(wrap_tcp(message)))


def _client_time(ping: bytes) -> Any:  # noqa: ANN401
    return read_parameter_table(BytesIO(ping[3:]))[1]


def server_frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    first = (0x80 if fin else 0) | opcode
    if len(payload) < 126:
        return struct.pack(">BB", first, len(payload)) + payload
    if len(payload) <= 0xFFFF:
        return struct.pack(">BBH", first, 126, len(payload)) + payload
    return struct.pack(">BBQ", first, 127, len(payload)) + payload


def read_client_frame(data: bytes) -> tuple[int, bytes, bytes] | None:
    if len(data) < 2:
        return None
    opcode, second = data[0] & 0x0F, data[1]
    assert second & 0x80, "client frames must be masked"
    length, offset = second & 0x7F, 2
    if length == 126:
        (length,), offset = struct.unpack_from(">H", data, 2), 4
    elif length == 127:
        (length,), offset = struct.unpack_from(">Q", data, 2), 10
    if len(data) < offset + 4 + length:
        return None
    mask = data[offset : offset + 4]
    body = data[offset + 4 : offset + 4 + length]
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(body))
    return opcode, payload, data[offset + 4 + length :]


def ws_peer(server: FakeWebSocketServer) -> tuple[PhotonPeer, Recorder]:
    recorder = Recorder()
    peer = PhotonPeer(recorder, WebSocketTransport(), keep_alive_interval=60)
    assert peer.connect(server.address, APP_ID)
    service_until(peer, lambda: peer.state == PeerState.Connected)
    return peer, recorder


def test_websocket_upgrade_replaces_the_init_request() -> None:
    server = FakeWebSocketServer(FakeServer())
    peer, recorder = ws_peer(server)
    assert server.request_line.startswith("GET /Master?")
    assert f"app={APP_ID.replace('-', '')}" in server.request_line
    assert server.headers["sec-websocket-protocol"] == "GpBinaryV18"
    assert peer.establish_encryption()
    service_until(peer, lambda: peer.is_encryption_available)
    big = bytes(70_000)  # 64-bit frame length both ways.
    assert peer.send_operation(1, {2: Int8SliceParameter(big)}, encrypt=True)
    service_until(peer, lambda: bool(recorder.events))
    assert recorder.responses[0].parameters[2] == Int8SliceParameter(big)
    assert not any(message[:2] == b"\xf3\x00" for message in server.messages)
    peer.disconnect()
    assert server.closed.wait(TIMEOUT)


def test_websocket_keeps_alive_with_the_ping_operation() -> None:
    server = FakeWebSocketServer(FakeServer())
    peer, recorder = ws_peer(server)
    peer.keep_alive_interval = 0
    service_until(peer, lambda: peer.last_round_trip_time is not None)
    assert not recorder.responses  # The ping response is internal.
    peer.disconnect()


def test_websocket_control_and_fragmented_frames() -> None:
    event = PacketFactory.event(
        cast("Any", ECHO_EVENT),
        cast("CommandParams", {2: StringParameter("split")}),
        protocol=SerializationProtocol.V18,
    ).serialize()[TCP_HEADER.size :]
    server = FakeWebSocketServer(
        FakeServer(),
        extra_frames=[
            server_frame(0x9, b"are you there"),
            server_frame(0x2, event[:5], fin=False),
            server_frame(0x0, event[5:]),
        ],
    )
    peer, recorder = ws_peer(server)
    service_until(peer, lambda: bool(recorder.events))
    assert recorder.events[0].parameters[2] == StringParameter("split")
    service_until(peer, lambda: bytes([0xA]) + b"are you there" in server.messages)
    peer.disconnect()


def test_websocket_rejected_upgrade() -> None:
    listener = socket.create_server(("127.0.0.1", 0))

    def refuse() -> None:
        conn, _ = listener.accept()
        with conn:
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")

    threading.Thread(target=refuse, daemon=True).start()
    recorder = Recorder()
    peer = PhotonPeer(recorder, WebSocketTransport())
    assert peer.connect(f"ws://127.0.0.1:{listener.getsockname()[1]}", APP_ID)
    service_until(peer, lambda: peer.state == PeerState.Disconnected)
    assert recorder.statuses == [StatusCode.DisconnectByServer]
    listener.close()


# -- peer and client support ------------------------------------------------------


class SecureFakeTransport(FakeTransport):
    secure: ClassVar[bool] = True


def test_secure_transport_skips_payload_encryption() -> None:
    server = FakeServer()
    recorder = Recorder()
    peer = PhotonPeer(recorder, SecureFakeTransport(server))
    assert peer.connect("gs.example:443", APP_ID)
    service_until(peer, lambda: peer.state == PeerState.Connected)
    assert peer.establish_encryption()
    service_until(peer, lambda: StatusCode.EncryptionEstablished in recorder.statuses)
    assert peer.is_encryption_available
    assert server.aes_key is None  # No key exchange happened.
    assert peer.send_operation(1, {}, encrypt=True)
    service_until(peer, lambda: bool(recorder.responses))
    assert not server.received[-1].get_header().is_encrypted()  # type: ignore[attr-defined]


def test_peer_puts_channel_and_reliability_in_the_tcp_header() -> None:
    server = FakeServer()
    sent: list[bytes] = []

    class Spy(FakeTransport):
        @override
        def send(self, data: bytes) -> None:
            sent.append(data)
            super().send(data)

    peer = PhotonPeer(Recorder(), Spy(server))
    assert peer.connect("gs.example:5055", APP_ID)
    service_until(peer, lambda: peer.state == PeerState.Connected)
    assert peer.send_operation(1, {}, reliable=False, channel=3)
    peer.service()
    assert sent[-1][5:7] == b"\x03\x00"


def test_client_creates_the_transport_for_the_protocol() -> None:
    client = RealtimeClient()
    assert isinstance(client.peer.transport, TcpTransport)
    client.connect_using_settings(
        AppSettings(
            app_id_realtime=APP_ID,
            name_server="127.0.0.1",
            protocol=ConnectionProtocol.WebSocketSecure,
        )
    )
    assert isinstance(client.peer.transport, SecureWebSocketTransport)
    assert client.name_server_address == "127.0.0.1:19093"
    client.disconnect()


def test_client_keeps_a_given_transport() -> None:
    transport = FakeTransport(FakeServer())
    client = RealtimeClient(transport)
    client.connect_using_settings(
        AppSettings(app_id_realtime=APP_ID, protocol=ConnectionProtocol.Udp)
    )
    assert client.peer.transport is transport
    assert client.protocol == ConnectionProtocol.Tcp
