"""``PhotonServer``: Name, Master and Game Server on local TCP ports."""

from __future__ import annotations

import contextlib
import logging
import secrets
import selectors
import socket
import threading
import time
from typing import TYPE_CHECKING, Any, Self, cast

from pyphotonrealtime.protocol.event_code import EventCode
from pyphotonrealtime.protocol.packet.init import InitRequestPacket
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.hashtable_param import HashtableParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.realtime.error_code import ErrorCode
from pyphotonrealtime.server.connection import Connection, Role, Session
from pyphotonrealtime.server.game_server import GameServer
from pyphotonrealtime.server.master_server import MasterServer
from pyphotonrealtime.server.name_server import NameServer
from pyphotonrealtime.server.rooms import REMOVED

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import TracebackType

    from pyphotonrealtime.peer import Parameters
    from pyphotonrealtime.server.handler import RoleHandler
    from pyphotonrealtime.server.rooms import Room

log = logging.getLogger(__name__)

DEFAULT_NAME_SERVER_PORT = 4533
DEFAULT_MASTER_SERVER_PORT = 4530
DEFAULT_GAME_SERVER_PORT = 4531
_RECV_CHUNK = 64 * 1024
_TICK = 0.05


class PhotonServer:
    """A self-hosted Photon Name, Master and Game Server, all over TCP.

    One background thread serves three listeners; rooms live in memory. Every
    app id is accepted unless ``app_id`` is given. Use as a context manager,
    or call :meth:`start` and :meth:`stop`::

        with PhotonServer(name_server_port=0) as server:
            settings = AppSettings(
                app_id_realtime=APP_ID,
                name_server=server.host,
                name_server_port=server.name_server_port,
                fixed_region="local",
            )

    Port 0 picks a free port; read the bound ones from the ``*_port``
    attributes after :meth:`start`.
    """

    def __init__(  # noqa: PLR0913 - one knob per listener
        self,
        host: str = "127.0.0.1",
        *,
        name_server_port: int = DEFAULT_NAME_SERVER_PORT,
        master_server_port: int = DEFAULT_MASTER_SERVER_PORT,
        game_server_port: int = DEFAULT_GAME_SERVER_PORT,
        public_host: str | None = None,
        regions: tuple[str, ...] = ("local",),
        app_id: str | None = None,
    ) -> None:
        """Configure the server; nothing listens until :meth:`start`.

        Args:
            host: Interface to bind.
            name_server_port: Name Server TCP port (0: any free port).
            master_server_port: Master Server TCP port (0: any free port).
            game_server_port: Game Server TCP port (0: any free port).
            public_host: Host handed to clients for the Master and Game
                Server hops; defaults to ``host`` (or 127.0.0.1 for 0.0.0.0).
            regions: Region codes the Name Server lists, all served here.
            app_id: Only accept this app id; None accepts any.
        """
        self.host = host
        self.name_server_port = name_server_port
        self.master_server_port = master_server_port
        self.game_server_port = game_server_port
        self.public_host = public_host or (
            "127.0.0.1" if host in {"", "0.0.0.0"} else host  # noqa: S104
        )
        self.regions = regions
        self.app_id = app_id
        self.rooms: dict[str, Room] = {}
        self.sessions: dict[str, Session] = {}
        self.connections: set[Connection] = set()
        self._handlers: dict[Role, RoleHandler] = {
            Role.NameServer: NameServer(self),
            Role.MasterServer: MasterServer(self),
            Role.GameServer: GameServer(self),
        }
        self._selector: selectors.BaseSelector | None = None
        self._listeners: list[socket.socket] = []
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.RLock()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> Self:
        """Bind the three listeners and serve them on a background thread.

        Returns:
            The server.
        """
        self._selector = selectors.DefaultSelector()
        for role in Role:
            listener = socket.create_server(
                (self.host, self._port(role)), reuse_port=False
            )
            listener.setblocking(False)  # noqa: FBT003
            self._set_port(role, listener.getsockname()[1])
            self._selector.register(listener, selectors.EVENT_READ, role)
            self._listeners.append(listener)
        self._running.set()
        self._thread = threading.Thread(
            target=self._serve, name="PhotonServer", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop serving and close every socket."""
        self._running.clear()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        for connection in list(self.connections):
            connection.sock.close()
        self.connections.clear()
        for listener in self._listeners:
            listener.close()
        self._listeners.clear()
        if self._selector is not None:
            self._selector.close()
            self._selector = None

    def __enter__(self) -> Self:
        """Start serving.

        Returns:
            The server.
        """
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop serving."""
        self.stop()

    @property
    def name_server_address(self) -> str:
        """``host:port`` of the Name Server, as clients should dial it."""
        return f"{self.public_host}:{self.name_server_port}"

    @property
    def master_server_address(self) -> str:
        """``host:port`` handed out for the Master Server."""
        return f"{self.public_host}:{self.master_server_port}"

    @property
    def game_server_address(self) -> str:
        """``host:port`` handed out for the Game Server."""
        return f"{self.public_host}:{self.game_server_port}"

    def _port(self, role: Role) -> int:
        return {
            Role.NameServer: self.name_server_port,
            Role.MasterServer: self.master_server_port,
            Role.GameServer: self.game_server_port,
        }[role]

    def _set_port(self, role: Role, port: int) -> None:
        if role == Role.NameServer:
            self.name_server_port = port
        elif role == Role.MasterServer:
            self.master_server_port = port
        else:
            self.game_server_port = port

    # -- shared state, used by the role handlers ---------------------------------

    def check_app_id(self, connection: Connection) -> bool:
        """Whether the connection's app id (from its Init) is accepted.

        Returns:
            True if accepted.
        """
        return self.app_id is None or connection.app_id == self.app_id.replace("-", "")

    def new_session(self, user_id: str) -> tuple[str, Session]:
        """Issue a token for ``user_id``.

        Returns:
            The token and its session.
        """
        token = secrets.token_hex(16)
        session = self.sessions[token] = Session(user_id, token)
        return token, session

    def connections_of(self, role: Role) -> Iterator[Connection]:
        """Open connections to the server playing ``role``.

        Yields:
            Each connection.
        """
        yield from (c for c in self.connections if c.role == role)

    def room_changed(self, room: Room, *, removed: bool = False) -> None:
        """Tell clients in the room's lobby about its new listing."""
        if removed or not room.listed:
            listing: HashtableParameter[Any, Any] = HashtableParameter(
                {Int8Parameter(REMOVED): BooleanParameter(value=True)}
            )
        else:
            listing = room.lobby_listing()
        params: Parameters = {
            ParameterKey.GameList: HashtableParameter(
                {StringParameter(room.name): listing}
            )
        }
        for connection in self.connections_of(Role.MasterServer):
            if connection.lobby == room.lobby:
                connection.send_event(EventCode.GameListUpdate, params)

    def remove_room(self, room: Room) -> None:
        """Drop ``room`` and delist it."""
        if self.rooms.get(room.name) is room:
            del self.rooms[room.name]
            self.room_changed(room, removed=True)

    # -- socket loop ---------------------------------------------------------------

    def _serve(self) -> None:
        assert self._selector is not None  # noqa: S101 -- set by start()
        while self._running.is_set():
            for key, _ in self._selector.select(_TICK):
                if isinstance(key.data, Role):
                    self._accept(key.fileobj, key.data)  # type: ignore[arg-type]
                else:
                    self._read(key.data)
            with self._lock:
                self._handlers[Role.GameServer].tick(time.monotonic())
                self._flush_all()

    def _accept(self, listener: socket.socket, role: Role) -> None:
        assert self._selector is not None  # noqa: S101 -- set by start()
        try:
            sock, peer = listener.accept()
        except BlockingIOError:
            return
        sock.setblocking(False)  # noqa: FBT003
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        connection = Connection(sock, role, f"{peer[0]}:{peer[1]}")
        self.connections.add(connection)
        self._selector.register(sock, selectors.EVENT_READ, connection)
        log.debug("%r connected", connection)

    def _read(self, connection: Connection) -> None:
        try:
            data = connection.sock.recv(_RECV_CHUNK)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._drop(connection)
            return
        with self._lock:
            try:
                packets = connection.feed(data)
            except (ValueError, TypeError) as exc:
                log.warning(
                    "%r sent garbage: %s (%s)", connection, exc, data[:64].hex(" ")
                )
                self._drop(connection)
                return
            handler = self._handlers[connection.role]
            for packet in packets:
                if isinstance(packet, PhotonOperationPacket):
                    self._dispatch(handler, connection, packet)
                elif isinstance(packet, InitRequestPacket):
                    handler.init_token(connection, packet.custom_init_data)

    def _dispatch(
        self,
        handler: RoleHandler,
        connection: Connection,
        packet: PhotonOperationPacket,
    ) -> None:
        payload = packet.get_payload()
        operation = int(payload.operation_code)
        encrypted = packet.get_header().is_encrypted()
        params = cast("Parameters", dict(payload.params))
        log.debug("%r op %d %s", connection, operation, params)
        try:
            handler.handle(connection, operation, params, encrypted=encrypted)
        except Exception:
            log.exception("%r: operation %d failed", connection, operation)
            connection.respond(
                operation,
                return_code=ErrorCode.InternalServerError,
                message="internal server error",
                encrypt=encrypted,
            )

    def _flush_all(self) -> None:
        for connection in list(self.connections):
            if connection.outbox:
                try:
                    sent = connection.sock.send(connection.outbox)
                except BlockingIOError:
                    continue
                except OSError:
                    self._drop(connection)
                    continue
                del connection.outbox[:sent]
            if connection.closing and not connection.outbox:
                self._drop(connection)

    def _drop(self, connection: Connection) -> None:
        if connection not in self.connections:
            return
        self.connections.discard(connection)
        if self._selector is not None:
            with contextlib.suppress(KeyError, ValueError):
                self._selector.unregister(connection.sock)
        connection.sock.close()
        log.debug("%r disconnected", connection)
        with self._lock:
            self._handlers[connection.role].disconnected(connection)
