"""Plain TCP transport (Photon's ``ConnectionProtocol.Tcp``)."""

import socket
from typing import override

from pyphotonrealtime.transport.base import Transport


class TcpTransport(Transport):
    """Non-blocking TCP socket.

    ``send`` only buffers; bytes the kernel doesn't accept right away stay in
    the buffer until a later ``send`` or ``flush``.
    """

    RECV_CHUNK = 64 * 1024

    def __init__(self) -> None:
        """Create an unconnected transport."""
        self._sock: socket.socket | None = None
        self._send_buffer = bytearray()

    @override
    def connect(self, host: str, port: int, timeout: float) -> None:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(0.0)  # Non-blocking from here on.
        self._sock = sock
        self._send_buffer.clear()

    @override
    def send(self, data: bytes) -> None:
        if self._sock is None:
            msg = "not connected"
            raise ConnectionError(msg)
        self._send_buffer.extend(data)
        self.flush()

    @override
    def flush(self) -> bool:
        if self._sock is None:
            return False
        while self._send_buffer:
            try:
                sent = self._sock.send(self._send_buffer)
            except (BlockingIOError, InterruptedError):
                break
            del self._send_buffer[:sent]
        return bool(self._send_buffer)

    @override
    def receive(self) -> bytes:
        if self._sock is None:
            return b""
        chunks: list[bytes] = []
        while True:
            try:
                chunk = self._sock.recv(self.RECV_CHUNK)
            except (BlockingIOError, InterruptedError):
                break
            if not chunk:
                self.close()
                break
            chunks.append(chunk)
        return b"".join(chunks)

    @override
    def close(self) -> None:
        self._send_buffer.clear()
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @property
    @override
    def connected(self) -> bool:
        return self._sock is not None
