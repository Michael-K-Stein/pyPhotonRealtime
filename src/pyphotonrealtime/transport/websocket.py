"""WebSocket transports (``ConnectionProtocol.WebSocket`` / ``WebSocketSecure``).

A minimal RFC 6455 client on top of :class:`TcpTransport`: each Photon
message travels as one binary frame, without TCP's 7-byte packet header.
The upgrade request carries what TCP's init request does (app id in the URL,
serialization protocol as the subprotocol), so the transport turns the
peer's first packet, the init request, into the upgrade instead of sending it.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import ssl
import struct
from typing import TYPE_CHECKING, ClassVar, override
from urllib.parse import urlencode

from pyphotonrealtime.protocol.packet.format import PacketFormat
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol
from pyphotonrealtime.transport.base import ConnectionProtocol
from pyphotonrealtime.transport.tcp import TcpTransport

if TYPE_CHECKING:
    import socket

# 0xFB, 4-byte length, channel, reliability flag; then the message.
TCP_HEADER_SIZE = 7
_TCP_HEADER = struct.Struct(">BIBB")
# Offsets in an init message (after 0xF3, 0x00) of the protocol and app id.
_INIT_PROTOCOL = 3
_INIT_APP_ID = 9
_APP_ID_LENGTH = 32

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_SUBPROTOCOLS = {
    SerializationProtocol.V16: "GpBinaryV16",
    SerializationProtocol.V18: "GpBinaryV18",
}
_LIB_VERSION = "5.0.14.0"
_HTTP_SWITCHING_PROTOCOLS = 101
_MAX_HEADER_BYTES = 16 * 1024

_FIN = 0x80
_MASKED = 0x80
_LENGTH_16 = 126
_LENGTH_64 = 127
_UINT16_MAX = 0xFFFF


class _Opcode:
    CONTINUATION = 0x0
    TEXT = 0x1
    BINARY = 0x2
    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA


class WebSocketTransport(TcpTransport):
    """Photon over plain WebSockets (``ws://``)."""

    protocol: ClassVar[ConnectionProtocol] = ConnectionProtocol.WebSocket
    ping_with_operation: ClassVar[bool] = True

    def __init__(self) -> None:
        """Create an unconnected transport."""
        super().__init__()
        self._host = ""
        self._port = 0
        self._open = False
        self._expected_accept = b""
        self._pending: list[bytes] = []
        """Messages queued before the upgrade completed."""
        self._raw = bytearray()
        self._message = bytearray()

    @override
    def connect(self, host: str, port: int, timeout: float) -> None:
        super().connect(host, port, timeout)
        self._host, self._port = host, port
        self._open = False
        self._expected_accept = b""
        self._pending.clear()
        self._raw.clear()
        self._message.clear()

    @override
    def _wrap(self, sock: socket.socket, host: str) -> socket.socket:
        if not self.secure:
            return sock
        return ssl.create_default_context().wrap_socket(sock, server_hostname=host)

    @override
    def send(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            if view[0] == PacketFormat.KeepAlive:
                view = view[5:]  # Pings travel as an operation instead.
                continue
            _, length, _, _ = _TCP_HEADER.unpack_from(view)
            self._send_message(bytes(view[TCP_HEADER_SIZE:length]))
            view = view[length:]

    def _send_message(self, message: bytes) -> None:
        if self._open:
            super().send(_frame(_Opcode.BINARY, message))
        elif not self._expected_accept:
            # The upgrade request replaces the init message; the server
            # answers it with an init response of its own.
            self._upgrade(message)
        else:
            self._pending.append(message)

    def _upgrade(self, init: bytes) -> None:
        protocol = SerializationProtocol(init[_INIT_PROTOCOL])
        app_id = init[_INIT_APP_ID : _INIT_APP_ID + _APP_ID_LENGTH].decode()
        key = base64.b64encode(os.urandom(16))
        self._expected_accept = base64.b64encode(hashlib.sha1(key + _GUID).digest())  # noqa: S324 - mandated by RFC 6455
        query = urlencode({"libversion": _LIB_VERSION, "sid": "", "app": app_id})
        request = (
            f"GET {self.path or '/'}?{query} HTTP/1.1\r\n"
            f"Host: {self._host}:{self._port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key.decode()}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: {_SUBPROTOCOLS[protocol]}\r\n"
            "\r\n"
        )
        super().send(request.encode())

    @override
    def receive(self) -> bytes:
        self._raw += super().receive()
        if not self._open and not self._read_upgrade_response():
            return b""
        out = bytearray()
        while (frame := self._next_frame()) is not None:
            opcode, payload = frame
            match opcode:
                case _Opcode.BINARY | _Opcode.TEXT | _Opcode.CONTINUATION:
                    message = bytes(payload)
                    out += _TCP_HEADER.pack(
                        PacketFormat.Data, len(message) + TCP_HEADER_SIZE, 0, 1
                    )
                    out += message
                case _Opcode.PING:
                    super().send(_frame(_Opcode.PONG, payload))
                case _Opcode.CLOSE:
                    self.close()
                    break
                case _:
                    pass  # Unsolicited pongs.
        return bytes(out)

    def _read_upgrade_response(self) -> bool:
        end = self._raw.find(b"\r\n\r\n")
        if end < 0:
            if len(self._raw) > _MAX_HEADER_BYTES:
                self.close()
            return False
        head = bytes(self._raw[:end]).decode("latin-1")
        del self._raw[: end + 4]
        status, *lines = head.split("\r\n")
        headers = {
            name.strip().lower(): value.strip()
            for name, _, value in (line.partition(":") for line in lines)
        }
        parts = status.split()
        if (
            len(parts) < 2  # noqa: PLR2004 - "HTTP/1.1 101"
            or parts[1] != str(_HTTP_SWITCHING_PROTOCOLS)
            or headers.get("sec-websocket-accept", "").encode() != self._expected_accept
        ):
            self.close()
            return False
        self._open = True
        for message in self._pending:
            super().send(_frame(_Opcode.BINARY, message))
        self._pending.clear()
        return True

    def _next_frame(self) -> tuple[int, bytes] | None:
        """Pop one complete message (control frames come through on their own).

        Returns:
            Opcode and payload, or None until more bytes arrive.
        """
        while True:
            parsed = _parse_frame(self._raw)
            if parsed is None:
                return None
            fin, opcode, payload, size = parsed
            del self._raw[:size]
            if opcode >= _Opcode.CLOSE:
                return opcode, payload
            self._message += payload
            if fin:
                message = bytes(self._message)
                self._message.clear()
                return _Opcode.BINARY, message

    @override
    def close(self) -> None:
        if self._open and self.connected:
            with contextlib.suppress(OSError):
                super().send(_frame(_Opcode.CLOSE, struct.pack(">H", 1000)))
        self._open = False
        self._pending.clear()
        super().close()


class SecureWebSocketTransport(WebSocketTransport):
    """Photon over TLS WebSockets (``wss://``), what browsers and consoles use."""

    protocol: ClassVar[ConnectionProtocol] = ConnectionProtocol.WebSocketSecure
    secure: ClassVar[bool] = True


def _frame(opcode: int, payload: bytes) -> bytes:
    """Build a masked client frame (RFC 6455 section 5.2).

    Returns:
        The frame.
    """
    length = len(payload)
    if length < _LENGTH_16:
        header = struct.pack(">BB", _FIN | opcode, _MASKED | length)
    elif length <= _UINT16_MAX:
        header = struct.pack(">BBH", _FIN | opcode, _MASKED | _LENGTH_16, length)
    else:
        header = struct.pack(">BBQ", _FIN | opcode, _MASKED | _LENGTH_64, length)
    mask = os.urandom(4)
    masked = (
        int.from_bytes(payload, "big")
        ^ int.from_bytes((mask * (length // 4 + 1))[:length], "big")
    ).to_bytes(length, "big")
    return header + mask + masked


def _parse_frame(data: bytearray) -> tuple[bool, int, bytes, int] | None:
    """Parse the frame at the start of ``data``.

    Returns:
        FIN flag, opcode, unmasked payload and the frame's size; None if
        ``data`` doesn't hold a whole frame yet.
    """
    if len(data) < 2:  # noqa: PLR2004 - smallest header
        return None
    first, second = data[0], data[1]
    length = second & 0x7F
    offset = 2
    if length == _LENGTH_16:
        if len(data) < offset + 2:
            return None
        (length,) = struct.unpack_from(">H", data, offset)
        offset += 2
    elif length == _LENGTH_64:
        if len(data) < offset + 8:
            return None
        (length,) = struct.unpack_from(">Q", data, offset)
        offset += 8
    mask = b""
    if second & _MASKED:
        if len(data) < offset + 4:
            return None
        mask = bytes(data[offset : offset + 4])
        offset += 4
    if len(data) < offset + length:
        return None
    payload = bytes(data[offset : offset + length])
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bool(first & _FIN), first & 0x0F, payload, offset + length
