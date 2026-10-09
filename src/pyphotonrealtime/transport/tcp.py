import socket
from typing import Optional

from pyphotonrealtime.transport.base import Transport


class TcpTransport(Transport):
    """Plain TCP transport (Photon's ``ConnectionProtocol.Tcp``)."""

    RECV_CHUNK = 64 * 1024

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None

    def connect(self, host: str, port: int, timeout: float) -> None:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setblocking(False)
        self._sock = sock

    def send(self, data: bytes) -> None:
        if self._sock is None:
            raise ConnectionError("not connected")
        # TODO: buffer partial writes instead of blocking.
        self._sock.setblocking(True)
        try:
            self._sock.sendall(data)
        finally:
            self._sock.setblocking(False)

    def receive(self) -> bytes:
        if self._sock is None:
            return b""
        chunks = []
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

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None
