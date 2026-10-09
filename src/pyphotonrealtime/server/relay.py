"""Byte-for-byte relay of a client connection to another server."""

from __future__ import annotations

import contextlib
import logging
import selectors
import socket
import threading

log = logging.getLogger(__name__)

_RECV_CHUNK = 64 * 1024
_TICK = 0.05
CONNECT_TIMEOUT = 10.0


class Relay(threading.Thread):
    """Pipes a client socket to ``upstream`` and back until either side closes.

    ``preamble`` is what the client already sent (its Init); it goes upstream
    first, so the real server sees the connection from its very first byte.
    """

    def __init__(
        self,
        client: socket.socket,
        upstream: tuple[str, int],
        preamble: bytes = b"",
    ) -> None:
        """Relay ``client`` to ``upstream``; call :meth:`start` to begin."""
        super().__init__(name=f"PhotonRelay-{upstream[0]}:{upstream[1]}", daemon=True)
        self.client = client
        self.upstream = upstream
        self.preamble = preamble
        self._closed = threading.Event()

    def run(self) -> None:
        """Connect upstream, then copy bytes both ways."""
        try:
            server = socket.create_connection(self.upstream, timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            log.warning("relay to %s:%d failed: %s", *self.upstream, exc)
            self.client.close()
            return
        with server, self.client, selectors.DefaultSelector() as selector:
            self.client.setblocking(True)  # noqa: FBT003
            server.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with contextlib.suppress(OSError):
                server.sendall(self.preamble)
                selector.register(self.client, selectors.EVENT_READ, server)
                selector.register(server, selectors.EVENT_READ, self.client)
                while not self._closed.is_set():
                    for key, _ in selector.select(_TICK):
                        source = key.fileobj
                        assert isinstance(source, socket.socket)  # noqa: S101
                        data = source.recv(_RECV_CHUNK)
                        if not data:
                            return
                        key.data.sendall(data)

    def close(self) -> None:
        """Stop relaying; both sockets close."""
        self._closed.set()
