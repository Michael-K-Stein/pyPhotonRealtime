"""One client connection to the self-hosted server: framing, crypto, replies."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, cast

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.packet.factory import PacketFactory
from pyphotonrealtime.protocol.packet.init import InitRequestPacket, InitResponsePacket
from pyphotonrealtime.protocol.packet.keep_alive import (
    PhotonKeepAliveRequest,
    PhotonKeepAliveResponse,
)
from pyphotonrealtime.protocol.packet.key_exchange import (
    InitEncryptionRequest,
    InitEncryptionResponse,
)
from pyphotonrealtime.protocol.packet.packet_stream import PhotonStreamParser
from pyphotonrealtime.protocol.photon_enc import generate_dh_keys

if TYPE_CHECKING:
    import socket
    from collections.abc import Callable

    from pyphotonrealtime.peer import Parameters
    from pyphotonrealtime.protocol.enum_lookups import CommandParams
    from pyphotonrealtime.protocol.event_code import EventCode
    from pyphotonrealtime.protocol.operation_code import OperationCode
    from pyphotonrealtime.protocol.packet.base import PhotonPacket
    from pyphotonrealtime.realtime.lobby import TypedLobby
    from pyphotonrealtime.server.rooms import Actor

_UINT32_MASK = 0xFFFFFFFF


class Role(Enum):
    """Which server a listener (and its connections) plays."""

    NameServer = auto()
    MasterServer = auto()
    GameServer = auto()


@dataclass(slots=True)
class Session:
    """What a token issued by the Name Server stands for."""

    user_id: str
    token: str
    secret: bytes | None = None
    """AuthOnce payload-encryption key, used instead of a key exchange."""
    lobby_stats: bool = False


@dataclass(slots=True, eq=False)
class Connection:
    """A connected client: parses its bytes and buffers what we send back."""

    sock: socket.socket
    role: Role
    address: str
    parser: PhotonStreamParser = field(default_factory=PhotonStreamParser)
    aes_key: bytes | None = None
    app_id: str = ""
    session: Session | None = None
    lobby: TypedLobby | None = None
    """Master Server: the lobby whose room list this client gets."""
    actor: Actor | None = None
    """Game Server: this client's player once it is in a room."""
    outbox: bytearray = field(default_factory=bytearray)
    closing: bool = False
    """Close once the outbox is flushed."""
    started: float = field(default_factory=time.monotonic)
    last_received: float = field(default_factory=time.monotonic)
    """When the client last sent anything; for the idle timeout."""
    preamble: bytearray | None = field(default_factory=bytearray)
    """Raw bytes received until the Init is accepted, for a passthrough relay."""
    passthrough: tuple[str, int] | None = None
    """Where to relay this client to instead of serving it."""

    @property
    def authenticated(self) -> bool:
        """Whether the client has passed Authenticate on this server."""
        return self.session is not None

    def feed(
        self,
        data: bytes,
        passthrough: Callable[[Connection, InitRequestPacket], tuple[str, int] | None]
        | None = None,
    ) -> list[PhotonPacket]:
        """Parse ``data``; answer the connection-level packets right here.

        ``passthrough`` sees the Init before it is answered; if it names an
        upstream server, parsing stops and :attr:`passthrough` is set: the
        caller relays :attr:`preamble` and the rest of the stream there.

        Returns:
            The operations left for the server role to handle.
        """
        self.last_received = time.monotonic()
        if passthrough is None:
            self.preamble = None
        elif self.preamble is not None:
            self.preamble += data
        self.parser.feed(data)
        operations: list[PhotonPacket] = []
        for packet in self.parser.parse(aes_key=self.aes_key):
            if isinstance(packet, InitRequestPacket):
                self.app_id = packet.app_id
                if passthrough is not None and self.preamble:
                    self.passthrough = passthrough(self, packet)
                    if self.passthrough is not None:
                        return []
                self.preamble = None
                self.send(InitResponsePacket())
                if packet.custom_init_data is not None:
                    operations.append(packet)  # The AuthOnce token.
            elif isinstance(packet, InitEncryptionRequest):
                public_key, self.aes_key = generate_dh_keys(packet.get_public_key())
                self.send(
                    InitEncryptionResponse(
                        public_key=public_key, protocol=self.parser.protocol
                    )
                )
            elif isinstance(packet, PhotonKeepAliveRequest):
                self.send(
                    PhotonKeepAliveResponse(
                        server_uptime=self._uptime_ms(),
                        client_time=packet.get_client_time(),
                    )
                )
            else:
                operations.append(packet)
        return operations

    def send(self, packet: PhotonPacket) -> None:
        """Queue ``packet``; the server loop writes it out."""
        self.outbox += packet.serialize()

    def respond(
        self,
        operation: int,
        params: Parameters | None = None,
        return_code: int = 0,
        message: str | None = None,
        *,
        encrypt: bool = False,
    ) -> None:
        """Send an operation response, encrypted if ``encrypt`` and possible."""
        encrypt = encrypt and self.aes_key is not None
        response = PacketFactory.operation(
            CommandCode.EncryptedOperationResponse
            if encrypt
            else CommandCode.OperationResponse,
            operation=cast("OperationCode", operation),
            params=cast("CommandParams", params or {}),
            return_code=return_code,
            error_message=message,
            protocol=self.parser.protocol,
        )
        if encrypt and self.aes_key is not None:
            response.set_aes_key(self.aes_key)
        self.send(response)

    def send_event(
        self, code: int, params: Parameters, *, encrypt: bool = False
    ) -> None:
        """Send an event, encrypted if ``encrypt`` and possible."""
        encrypt = encrypt and self.aes_key is not None
        event = PacketFactory.event(
            cast("EventCode", code),
            cast("CommandParams", params),
            encrypted=encrypt,
            protocol=self.parser.protocol,
        )
        if encrypt and self.aes_key is not None:
            event.set_aes_key(self.aes_key)
        self.send(event)

    def close_after_flush(self) -> None:
        """Disconnect this client once everything queued is sent."""
        self.closing = True

    def _uptime_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000) & _UINT32_MASK

    def __repr__(self) -> str:
        """Role and peer address."""
        return f"Connection({self.role.name}, {self.address})"
