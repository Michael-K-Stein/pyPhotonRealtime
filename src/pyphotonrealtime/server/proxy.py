"""``PhotonProxy``: a man in the middle between Photon clients and a server.

Each client that connects gets its own connection to the upstream server.
Every packet in either direction is decoded and handed to ``on_packet``,
which may log it, return it changed or replaced, or drop it; the session
can also inject packets of its own. Encrypted operations are readable too:
the proxy runs its own key exchange with each side and re-encrypts.

The proxy covers one hop. Photon hands out Master and Game Server
addresses in operation responses; to follow a client there, rewrite
``ParameterKey.Address`` in ``on_packet`` to another proxy.
"""

from __future__ import annotations

import contextlib
import logging
import selectors
import socket
import threading
from enum import Enum, auto
from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.packet.init import InitRequestPacket
from pyphotonrealtime.protocol.packet.key_exchange import (
    InitEncryptionRequest,
    InitEncryptionResponse,
)
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.packet.packet_stream import PhotonStreamParser
from pyphotonrealtime.protocol.photon_enc import (
    build_dh_request,
    generate_dh_keys,
    process_dh_response,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from pyphotonrealtime.protocol.packet.base import PhotonPacket

log = logging.getLogger(__name__)

_RECV_CHUNK = 64 * 1024
_TICK = 0.05


class Direction(Enum):
    """Which way a packet travels through the proxy."""

    ToServer = auto()
    ToClient = auto()


type PacketHook = Callable[[ProxySession, Direction, PhotonPacket], PhotonPacket | None]
"""Sees each packet; returns what to forward (it, or a replacement) or None to drop."""


class ProxySession:
    """One client and its upstream connection.

    Created by :class:`PhotonProxy`; use :meth:`send_to_server` and
    :meth:`send_to_client` to inject packets.
    """

    def __init__(self, client: socket.socket, server: socket.socket) -> None:
        """Pair ``client`` with its ``server`` connection."""
        self.client = client
        self.server = server
        self.client_parser = PhotonStreamParser()
        self.server_parser = PhotonStreamParser()
        self.client_key: bytes | None = None
        """AES key shared with the client."""
        self.server_key: bytes | None = None
        """AES key shared with the upstream server."""
        self._client_public_key: bytes | None = None
        self._dh_private_key: int | None = None
        self._lock = threading.Lock()

    def send_to_server(self, packet: PhotonPacket) -> None:
        """Send ``packet`` upstream, encrypted with the server key if it is."""
        self._send(self.server, packet, self.server_key)

    def send_to_client(self, packet: PhotonPacket) -> None:
        """Send ``packet`` to the client, encrypted with the client key if it is."""
        self._send(self.client, packet, self.client_key)

    def _send(
        self, sock: socket.socket, packet: PhotonPacket, key: bytes | None
    ) -> None:
        if isinstance(packet, PhotonOperationPacket) and key is not None:
            packet.set_aes_key(key)
        data = packet.serialize()
        with self._lock:
            sock.sendall(data)

    def from_client(self, data: bytes, hook: PacketHook | None) -> None:
        """Handle bytes the client sent."""
        snapshot = bytes(self.client_parser.buffer) + data
        self.client_parser.feed(data)
        before = len(snapshot)
        offset = 0
        for packet in self.client_parser.parse(aes_key=self.client_key):
            size = before - offset - len(self.client_parser.buffer)
            raw, offset = snapshot[offset : offset + size], offset + size
            if isinstance(packet, InitEncryptionRequest):
                # Our own exchange with each side; the client's answer waits
                # for the server's, so nothing is sent before both keys exist.
                self._client_public_key = packet.get_public_key()
                self._dh_private_key, request = build_dh_request(
                    protocol=self.client_parser.protocol
                )
                self.send_to_server(request)
                continue
            self._forward(Direction.ToServer, packet, raw, hook)

    def from_server(self, data: bytes, hook: PacketHook | None) -> None:
        """Handle bytes the upstream server sent."""
        snapshot = bytes(self.server_parser.buffer) + data
        self.server_parser.feed(data)
        before = len(snapshot)
        offset = 0
        for packet in self.server_parser.parse(
            expect_responses=True, aes_key=self.server_key
        ):
            size = before - offset - len(self.server_parser.buffer)
            raw, offset = snapshot[offset : offset + size], offset + size
            if isinstance(packet, InitEncryptionResponse):
                self._key_exchanged(packet)
                continue
            self._forward(Direction.ToClient, packet, raw, hook)

    def _key_exchanged(self, response: InitEncryptionResponse) -> None:
        if self._dh_private_key is None or self._client_public_key is None:
            log.warning("unexpected key exchange response; dropped")
            return
        self.server_key = process_dh_response(self._dh_private_key, response)
        public_key, self.client_key = generate_dh_keys(self._client_public_key)
        self._dh_private_key = self._client_public_key = None
        self.send_to_client(
            InitEncryptionResponse(
                public_key=public_key, protocol=self.client_parser.protocol
            )
        )

    def _forward(
        self,
        direction: Direction,
        packet: PhotonPacket,
        raw: bytes,
        hook: PacketHook | None,
    ) -> None:
        if isinstance(packet, InitRequestPacket):
            # The server answers in the client's protocol from now on.
            self.server_parser.protocol = packet.serialization_protocol
        forward = packet if hook is None else hook(self, direction, packet)
        if forward is None:
            return
        send = (
            self.send_to_server
            if direction == Direction.ToServer
            else self.send_to_client
        )
        if forward is packet and not isinstance(packet, PhotonOperationPacket):
            # Init (which may be HTTP-style) and keep-alives go out as they came.
            target = self.server if direction == Direction.ToServer else self.client
            with self._lock:
                target.sendall(raw)
        else:
            send(forward)

    def close(self) -> None:
        """Close both connections."""
        self.client.close()
        self.server.close()


class PhotonProxy:
    """Accepts Photon clients and proxies each one to ``upstream``.

    Use as a context manager, or call :meth:`start` and :meth:`stop`::

        def show(session, direction, packet):
            print(direction.name, *packet.log())
            return packet


        with PhotonProxy(("203.0.113.5", 4533), on_packet=show) as proxy:
            ...  # point the client at proxy.host:proxy.port
    """

    def __init__(
        self,
        upstream: tuple[str, int],
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        on_packet: PacketHook | None = None,
    ) -> None:
        """Configure the proxy; nothing listens until :meth:`start`.

        Args:
            upstream: ``(host, port)`` of the real server.
            host: Interface to listen on.
            port: Port to listen on (0: any free port; read :attr:`port`).
            on_packet: Called on the proxy thread for every packet, both
                ways, except the key exchange. Return the packet (changed
                or not) or a replacement to forward it, None to drop it.
        """
        self.upstream = upstream
        self.host = host
        self.port = port
        self.on_packet = on_packet
        self.sessions: set[ProxySession] = set()
        self._listener: socket.socket | None = None
        self._selector: selectors.BaseSelector | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    def start(self) -> Self:
        """Listen and proxy on a background thread.

        Returns:
            The proxy.
        """
        self._listener = socket.create_server((self.host, self.port))
        self._listener.setblocking(False)  # noqa: FBT003
        self.port = self._listener.getsockname()[1]
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._listener, selectors.EVENT_READ)
        self._running.set()
        self._thread = threading.Thread(
            target=self._serve, name="PhotonProxy", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop proxying and close every socket."""
        self._running.clear()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        for session in self.sessions:
            session.close()
        self.sessions.clear()
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._selector is not None:
            self._selector.close()
            self._selector = None

    def __enter__(self) -> Self:
        """Start proxying.

        Returns:
            The proxy.
        """
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop proxying."""
        self.stop()

    def _serve(self) -> None:
        assert self._selector is not None  # noqa: S101 -- set by start()
        while self._running.is_set():
            for key, _ in self._selector.select(_TICK):
                if key.data is None:
                    self._accept()
                else:
                    session, direction = key.data
                    self._read(session, direction)

    def _accept(self) -> None:
        assert self._listener is not None  # noqa: S101 -- set by start()
        assert self._selector is not None  # noqa: S101
        try:
            client, _ = self._listener.accept()
        except BlockingIOError:
            return
        try:
            server = socket.create_connection(self.upstream, timeout=10)
        except OSError as exc:
            log.warning("upstream %s:%d unreachable: %s", *self.upstream, exc)
            client.close()
            return
        for sock in (client, server):
            sock.setblocking(True)  # noqa: FBT003
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        session = ProxySession(client, server)
        self.sessions.add(session)
        self._selector.register(
            client, selectors.EVENT_READ, (session, Direction.ToServer)
        )
        self._selector.register(
            server, selectors.EVENT_READ, (session, Direction.ToClient)
        )

    def _read(self, session: ProxySession, direction: Direction) -> None:
        source = session.client if direction == Direction.ToServer else session.server
        try:
            data = source.recv(_RECV_CHUNK)
            if data:
                if direction == Direction.ToServer:
                    session.from_client(data, self.on_packet)
                else:
                    session.from_server(data, self.on_packet)
                return
        except (OSError, ValueError, TypeError) as exc:
            log.warning("proxy session failed: %s", exc)
        self._close(session)

    def _close(self, session: ProxySession) -> None:
        assert self._selector is not None  # noqa: S101 -- set by start()
        for sock in (session.client, session.server):
            with contextlib.suppress(KeyError, ValueError):
                self._selector.unregister(sock)
        session.close()
        self.sessions.discard(session)
