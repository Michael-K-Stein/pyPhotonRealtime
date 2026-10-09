"""UDP transport (``ConnectionProtocol.Udp``): Photon's ENet-style reliability layer.

Every datagram starts with a 12-byte header (peer id, CRC flag, command
count, sent time, challenge) and holds one or more commands, each with a
12-byte header of its own (type, channel, flags, reserved, length, reliable
sequence number). The client opens with ``CONNECT``; the server's
``VERIFY_CONNECT`` assigns the peer id. After that, Photon messages travel as
reliable or unreliable commands on a channel. Reliable commands are acked,
resent until acked, and delivered in order per channel; messages that don't
fit one datagram are split into reliable fragments.

The upper edge speaks Photon's TCP framing like every transport: the TCP
header's channel and reliability bytes pick the command, ``0xF0`` pings
become ``PING`` commands, and the ack of a ``PING`` comes back up as a ping
response so the peer measures round-trip time as usual.
"""

from __future__ import annotations

import contextlib
import os
import socket
import struct
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar, override

from pyphotonrealtime.protocol.packet.format import PacketFormat
from pyphotonrealtime.transport.base import ConnectionProtocol, Transport

_PACKET_HEADER = struct.Struct(">HBBII")  # peer id, crc, commands, sent time, challenge
_CRC_SIZE = 4
_CRC_ENABLED = 0xCC
_COMMAND_HEADER = struct.Struct(
    ">BBBBII"
)  # type, channel, flags, reserved, length, seq
_RESERVED = 4
_ACK = struct.Struct(">II")  # acked reliable sequence number, acked sent time
_FRAGMENT = struct.Struct(">IIIII")  # start seq, count, number, total length, offset
_UINT32 = struct.Struct(">I")
_TCP_HEADER = struct.Struct(">BIBB")  # 0xFB, length, channel, reliable
_TCP_HEADER_SIZE = _TCP_HEADER.size
_PING_RESPONSE = struct.Struct(">BII")  # 0xF0, server time, client time
_KEEP_ALIVE_SIZE = 5

_UNASSIGNED_PEER_ID = 0xFFFF
_SYSTEM_CHANNEL = 0xFF
_TIME_MASK = 0x7FFFFFFF


class CommandType(IntEnum):
    """ENet command types as Photon uses them."""

    Ack = 1
    Connect = 2
    VerifyConnect = 3
    Disconnect = 4
    Ping = 5
    SendReliable = 6
    SendUnreliable = 7
    SendFragment = 8
    SendUnsequenced = 11


class _Flag:
    UNRELIABLE = 0
    RELIABLE = 1


@dataclass(slots=True)
class _Outgoing:
    """A reliable command waiting for its ack."""

    data: bytes
    channel: int
    sequence: int
    sent_at: float = 0.0
    timeout: float = 0.0
    sends: int = 0
    ping_time: int | None = None
    """Client time of a keep-alive ``PING``, reported up when it's acked."""


@dataclass(slots=True)
class _Channel:
    outgoing_reliable: int = 0
    outgoing_unreliable: int = 0
    incoming_reliable: int = 0
    """Last reliable sequence number delivered upward."""
    incoming_unreliable: int = 0
    received: dict[int, tuple[int, bytes]] = field(default_factory=dict)
    """Reliable commands that arrived ahead of order: seq -> (type, payload)."""
    fragments: dict[int, _Assembly] = field(default_factory=dict)


@dataclass(slots=True)
class _Assembly:
    data: bytearray
    missing: int


class UdpTransport(Transport):
    """Photon over UDP with reliable and unreliable channels."""

    protocol: ClassVar[ConnectionProtocol] = ConnectionProtocol.Udp

    MTU = 1200
    MAX_SENDS = 8
    """Give up on the connection when a reliable command needs more sends."""
    MIN_RESEND_TIMEOUT = 0.05
    MAX_RESEND_TIMEOUT = 2.0

    def __init__(self, channel_count: int = 2) -> None:
        """Create an unconnected transport.

        Args:
            channel_count: Channels announced to the server; the SDKs use 2.
        """
        self.channel_count = channel_count
        self._sock: socket.socket | None = None
        self._peer_id = _UNASSIGNED_PEER_ID
        self._challenge = 0
        self._verified = False
        self._epoch = time.monotonic()
        self._channels: dict[int, _Channel] = {}
        self._unacked: dict[tuple[int, int], _Outgoing] = {}
        self._queue: list[bytes] = []
        """Commands to put in the next datagram."""
        self._held: list[tuple[bytes, int, bool]] = []
        """Messages sent before ``VERIFY_CONNECT``: (message, channel, reliable)."""
        self._inbox = bytearray()
        self._round_trip_time = 0.3

    # -- Transport --------------------------------------------------------

    @override
    def connect(self, host: str, port: int, timeout: float) -> None:
        family, kind, proto, _, address = socket.getaddrinfo(
            host, port, type=socket.SOCK_DGRAM
        )[0]
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(address)
            sock.setblocking(False)  # noqa: FBT003
        except OSError:
            sock.close()
            raise
        self._sock = sock
        self._peer_id = _UNASSIGNED_PEER_ID
        self._challenge = int.from_bytes(os.urandom(4)) & _TIME_MASK
        self._verified = False
        self._channels.clear()
        self._unacked.clear()
        self._queue.clear()
        self._held.clear()
        self._inbox.clear()
        self._send_reliable(CommandType.Connect, _SYSTEM_CHANNEL, self._connect_data())
        self._flush()

    @override
    def send(self, data: bytes) -> None:
        if self._sock is None:
            msg = "not connected"
            raise ConnectionError(msg)
        view = memoryview(data)
        while view:
            if view[0] == PacketFormat.KeepAlive:
                (client_time,) = _UINT32.unpack_from(view, 1)
                if self._verified:
                    self._send_reliable(
                        CommandType.Ping, _SYSTEM_CHANNEL, ping_time=client_time
                    )
                view = view[_KEEP_ALIVE_SIZE:]
                continue
            _, length, channel, reliable = _TCP_HEADER.unpack_from(view)
            message = bytes(view[_TCP_HEADER_SIZE:length])
            if self._verified:
                self._send_message(message, channel, reliable=bool(reliable))
            else:
                self._held.append((message, channel, bool(reliable)))
            view = view[length:]
        self._flush()

    @override
    def flush(self) -> bool:
        self._flush()
        return False

    @override
    def receive(self) -> bytes:
        while self._sock is not None:
            try:
                datagram = self._sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:  # E.g. ICMP port unreachable on Windows.
                self.close()
                break
            self._handle_datagram(datagram)
        self._flush()
        data, self._inbox = bytes(self._inbox), bytearray()
        return data

    @override
    def close(self) -> None:
        if self._sock is None:
            return
        if self._verified:
            channel = self._channel(_SYSTEM_CHANNEL)
            self._queue.append(
                _command(
                    CommandType.Disconnect,
                    _SYSTEM_CHANNEL,
                    _Flag.UNRELIABLE,
                    channel.outgoing_reliable,
                )
            )
            self._unacked.clear()
            with contextlib.suppress(OSError):
                self._flush()
        sock, self._sock = self._sock, None
        self._verified = False
        sock.close()

    @property
    @override
    def connected(self) -> bool:
        return self._sock is not None

    # -- sending ----------------------------------------------------------

    def _connect_data(self) -> bytes:
        data = bytearray(32)
        struct.pack_into(">H", data, 2, self.MTU)
        struct.pack_into(">I", data, 4, 0x8000)  # Window size.
        data[11] = self.channel_count
        struct.pack_into(">I", data, 20, 5000)
        data[27] = 2
        data[31] = 2
        return bytes(data)

    def _channel(self, number: int) -> _Channel:
        channel = self._channels.get(number)
        if channel is None:
            channel = self._channels[number] = _Channel()
        return channel

    def _send_message(self, message: bytes, channel: int, *, reliable: bool) -> None:
        room = self.MTU - _PACKET_HEADER.size - _COMMAND_HEADER.size
        if len(message) > room - _UINT32.size:
            self._send_fragments(message, channel)
        elif reliable:
            self._send_reliable(CommandType.SendReliable, channel, message)
        else:
            state = self._channel(channel)
            state.outgoing_unreliable += 1
            self._queue.append(
                _command(
                    CommandType.SendUnreliable,
                    channel,
                    _Flag.UNRELIABLE,
                    state.outgoing_reliable,
                    _UINT32.pack(state.outgoing_unreliable) + message,
                )
            )

    def _send_fragments(self, message: bytes, channel: int) -> None:
        size = self.MTU - _PACKET_HEADER.size - _COMMAND_HEADER.size - _FRAGMENT.size
        count = (len(message) + size - 1) // size
        start = self._channel(channel).outgoing_reliable + 1
        for number in range(count):
            offset = number * size
            header = _FRAGMENT.pack(start, count, number, len(message), offset)
            self._send_reliable(
                CommandType.SendFragment,
                channel,
                header + message[offset : offset + size],
            )

    def _send_reliable(
        self,
        command_type: CommandType,
        channel: int,
        payload: bytes = b"",
        *,
        ping_time: int | None = None,
    ) -> None:
        state = self._channel(channel)
        state.outgoing_reliable += 1
        data = _command(
            command_type, channel, _Flag.RELIABLE, state.outgoing_reliable, payload
        )
        self._unacked[channel, state.outgoing_reliable] = _Outgoing(
            data, channel, state.outgoing_reliable, ping_time=ping_time
        )

    def _flush(self) -> None:
        if self._sock is None:
            return
        now = time.monotonic()
        for outgoing in list(self._unacked.values()):
            if outgoing.sends and now - outgoing.sent_at < outgoing.timeout:
                continue
            if outgoing.sends >= self.MAX_SENDS:
                self.close()
                return
            outgoing.timeout = min(
                max(2 * self._round_trip_time, self.MIN_RESEND_TIMEOUT)
                * 2**outgoing.sends,
                self.MAX_RESEND_TIMEOUT,
            )
            outgoing.sends += 1
            outgoing.sent_at = now
            self._queue.append(outgoing.data)
        self._send_datagrams()

    def _send_datagrams(self) -> None:
        assert self._sock is not None  # noqa: S101 -- checked by callers
        room = self.MTU - _PACKET_HEADER.size
        while self._queue:
            batch: list[bytes] = []
            size = 0
            while self._queue and (not batch or size + len(self._queue[0]) <= room):
                command = self._queue.pop(0)
                batch.append(command)
                size += len(command)
            header = _PACKET_HEADER.pack(
                self._peer_id, 0, len(batch), self._time(), self._challenge
            )
            try:
                self._sock.send(header + b"".join(batch))
            except (BlockingIOError, InterruptedError):
                return  # Reliable commands get resent; acks get re-requested.

    # -- receiving --------------------------------------------------------

    def _handle_datagram(self, datagram: bytes) -> None:
        if len(datagram) < _PACKET_HEADER.size:
            return
        _, crc, count, sent_time, challenge = _PACKET_HEADER.unpack_from(datagram)
        if challenge != self._challenge:
            return  # Stale or spoofed.
        offset = _PACKET_HEADER.size + (_CRC_SIZE if crc == _CRC_ENABLED else 0)
        for _ in range(count):
            if offset + _COMMAND_HEADER.size > len(datagram):
                return
            command_type, channel, flags, _, length, sequence = (
                _COMMAND_HEADER.unpack_from(datagram, offset)
            )
            if length < _COMMAND_HEADER.size or offset + length > len(datagram):
                return
            payload = datagram[offset + _COMMAND_HEADER.size : offset + length]
            offset += length
            if flags & _Flag.RELIABLE:
                self._queue.append(
                    _command(
                        CommandType.Ack,
                        channel,
                        _Flag.UNRELIABLE,
                        sequence,
                        _ACK.pack(sequence, sent_time),
                    )
                )
            self._handle_command(command_type, channel, sequence, payload)
            if self._sock is None:
                return

    def _handle_command(
        self, command_type: int, channel: int, sequence: int, payload: bytes
    ) -> None:
        match command_type:
            case CommandType.Ack:
                self._on_ack(channel, payload)
            case CommandType.VerifyConnect:
                self._on_verify_connect(payload)
            case CommandType.Disconnect:
                self._unacked.clear()
                self._verified = False
                self.close()
            case CommandType.SendReliable | CommandType.SendFragment:
                self._on_reliable(command_type, channel, sequence, payload)
            case CommandType.SendUnreliable:
                self._on_unreliable(channel, sequence, payload)
            case CommandType.SendUnsequenced:
                self._deliver(payload[_UINT32.size :], channel)
            case _:
                pass  # Pings only need their ack.

    def _on_ack(self, channel: int, payload: bytes) -> None:
        if len(payload) < _ACK.size:
            return
        sequence, sent_time = _ACK.unpack_from(payload)
        outgoing = self._unacked.pop((channel, sequence), None)
        if outgoing is None:
            return
        rtt = ((self._time() - sent_time) & _TIME_MASK) / 1000
        self._round_trip_time += (rtt - self._round_trip_time) / 8
        if outgoing.ping_time is not None:
            self._inbox += _PING_RESPONSE.pack(
                PacketFormat.KeepAlive, 0, outgoing.ping_time
            )

    def _on_verify_connect(self, payload: bytes) -> None:
        if self._verified or len(payload) < 2:  # noqa: PLR2004 - peer id
            return
        (self._peer_id,) = struct.unpack_from(">H", payload)
        self._verified = True
        held, self._held = self._held, []
        for message, channel, reliable in held:
            self._send_message(message, channel, reliable=reliable)

    def _on_reliable(
        self, command_type: int, channel: int, sequence: int, payload: bytes
    ) -> None:
        state = self._channel(channel)
        if sequence <= state.incoming_reliable:
            return  # Duplicate; acked again above.
        state.received[sequence] = (command_type, payload)
        while (
            ready := state.received.pop(state.incoming_reliable + 1, None)
        ) is not None:
            state.incoming_reliable += 1
            state.incoming_unreliable = 0
            kind, data = ready
            if kind == CommandType.SendReliable:
                self._deliver(data, channel)
            else:
                self._on_fragment(state, channel, data)

    def _on_fragment(self, state: _Channel, channel: int, data: bytes) -> None:
        if len(data) < _FRAGMENT.size:
            return
        start, count, _, total, offset = _FRAGMENT.unpack_from(data)
        chunk = data[_FRAGMENT.size :]
        assembly = state.fragments.get(start)
        if assembly is None:
            assembly = state.fragments[start] = _Assembly(bytearray(total), count)
        assembly.data[offset : offset + len(chunk)] = chunk
        assembly.missing -= 1
        if assembly.missing == 0:
            del state.fragments[start]
            self._deliver(bytes(assembly.data), channel)

    def _on_unreliable(self, channel: int, sequence: int, payload: bytes) -> None:
        if len(payload) < _UINT32.size:
            return
        state = self._channel(channel)
        (unreliable,) = _UINT32.unpack_from(payload)
        # Only after every reliable command it follows, and never out of order.
        if (
            sequence != state.incoming_reliable
            or unreliable <= state.incoming_unreliable
        ):
            return
        state.incoming_unreliable = unreliable
        self._deliver(payload[_UINT32.size :], channel)

    def _deliver(self, message: bytes, channel: int) -> None:
        self._inbox += _TCP_HEADER.pack(
            PacketFormat.Data, len(message) + _TCP_HEADER_SIZE, channel, 1
        )
        self._inbox += message

    def _time(self) -> int:
        return int((time.monotonic() - self._epoch) * 1000) & _TIME_MASK


def _command(
    command_type: int, channel: int, flags: int, sequence: int, payload: bytes = b""
) -> bytes:
    return (
        _COMMAND_HEADER.pack(
            command_type,
            channel,
            flags,
            _RESERVED,
            _COMMAND_HEADER.size + len(payload),
            sequence,
        )
        + payload
    )
