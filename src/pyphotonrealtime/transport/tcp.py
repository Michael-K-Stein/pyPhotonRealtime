"""Plain TCP transport (Photon's ``ConnectionProtocol.Tcp``)."""

import socket
from typing import override

from pyphotonrealtime.transport.base import Transport


class TcpTransport(Transport):
    """Non-blocking TCP socket.

    Sends are currently blocking ``sendall`` calls; buffering partial writes is
    tracked in ROADMAP.md (M1).
    """

    RECV_CHUNK = 64 * 1024

    def __init__(self) -> None:
        """Create an unconnected transport."""
        self._sock: socket.socket | None = None

    @override
    def connect(self, host: str, port: int, timeout: float) -> None:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(0.0)  # Non-blocking from here on.
        self._sock = sock

    @override
    def send(self, data: bytes) -> None:
        if self._sock is None:
            msg = "not connected"
            raise ConnectionError(msg)
        self._sock.settimeout(None)
        try:
            self._sock.sendall(data)
        finally:
            self._sock.settimeout(0.0)

    @override
    def receive(self) -> bytes:
        if self._sock is None:
            return b""
        chunks: list[bytes] = []
        while True:
            try:
                chunk = self._sock.recv(self.RECV_CHUNK)
            except BlockingIOError:
                break
            if not chunk:
                self.close()
                break
            chunks.append(chunk)
        return b"".join(chunks)

    @override
    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @property
    @override
    def connected(self) -> bool:
        return self._sock is not None
