"""``PhotonPeer``: a single connection to a single Photon server.

Mirrors the official SDKs' service loop: nothing happens on the network unless
the application calls :meth:`PhotonPeer.service` (or the two halves,
:meth:`PhotonPeer.dispatch_incoming_commands` and
:meth:`PhotonPeer.send_outgoing_commands`) regularly, typically 10-50 times per
second, from a single thread.

Callbacks to the :class:`PeerListener` are only ever invoked from inside
``dispatch_incoming_commands``, i.e. on the thread that calls ``service``.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Protocol

from pyphotonrealtime.protocol.packet.packet_stream import PhotonStreamParser
from pyphotonrealtime.transport import TcpTransport

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.packet.base import PhotonPacket
    from pyphotonrealtime.protocol.param.base import ParameterBase
    from pyphotonrealtime.transport import Transport

type Parameters = dict[int, ParameterBase[Any]]


class PeerState(Enum):
    """Connection state of a :class:`PhotonPeer`."""

    Disconnected = auto()
    Connecting = auto()
    Connected = auto()
    Disconnecting = auto()


class StatusCode(Enum):
    """Peer status changes reported to :meth:`PeerListener.on_status_changed`."""

    Connect = auto()
    Disconnect = auto()
    ExceptionOnConnect = auto()
    Exception = auto()
    TimeoutDisconnect = auto()
    DisconnectByServer = auto()
    EncryptionEstablished = auto()
    EncryptionFailedToEstablish = auto()


@dataclass(frozen=True, slots=True)
class OperationResponse:
    """The server's answer to an operation sent with ``send_operation``."""

    operation_code: int
    return_code: int
    debug_message: str | None
    parameters: Parameters = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EventData:
    """A server- or player-originated event."""

    code: int
    parameters: Parameters = field(default_factory=dict)


class PeerListener(Protocol):
    """Receives everything a peer dispatches (``IPhotonPeerListener``)."""

    def on_status_changed(self, status: StatusCode) -> None:
        """Handle a connection status change."""

    def on_operation_response(self, response: OperationResponse) -> None:
        """Handle the response to an operation."""

    def on_event(self, event: EventData) -> None:
        """Handle an incoming event."""


class PhotonPeer:
    """One connection to one Photon server, driven by :meth:`service`."""

    def __init__(
        self,
        listener: PeerListener,
        transport: Transport | None = None,
        *,
        disconnect_timeout: float = 10.0,
        keep_alive_interval: float = 2.0,
    ) -> None:
        """Create a disconnected peer.

        Args:
            listener: Receives status changes, responses and events.
            transport: Byte transport to use; defaults to TCP.
            disconnect_timeout: Seconds without traffic before giving up.
            keep_alive_interval: Seconds of idle sending before a ping.
        """
        self.listener = listener
        self.transport = transport if transport is not None else TcpTransport()
        self.disconnect_timeout = disconnect_timeout
        self.keep_alive_interval = keep_alive_interval

        self.state = PeerState.Disconnected
        self.server_address: str | None = None
        self.app_id: str | None = None

        self._parser = PhotonStreamParser()
        self._outgoing: deque[PhotonPacket] = deque()
        self._incoming: deque[PhotonPacket] = deque()
        self._aes_key: bytes | None = None
        self._last_receive = 0.0
        self._last_send = 0.0

    # -- connection -------------------------------------------------------

    def connect(self, server_address: str, app_id: str) -> bool:
        """Start connecting to ``host:port``; the result arrives as a status.

        Returns:
            Whether the connection attempt was started.
        """
        # M1: send InitRequestPacket(app id, protocol version), move to
        # Connected on InitResponse.
        raise NotImplementedError

    def disconnect(self) -> None:
        """Send a disconnect message, then close the transport."""
        raise NotImplementedError

    def establish_encryption(self) -> bool:
        """Start a Diffie-Hellman key exchange (``InitEncryptionRequest``).

        Returns:
            Whether the exchange was started.
        """
        raise NotImplementedError

    @property
    def is_encryption_available(self) -> bool:
        """Whether a shared AES key has been negotiated."""
        return self._aes_key is not None

    # -- service loop -----------------------------------------------------

    def service(self) -> None:
        """Dispatch everything received, then send everything queued."""
        while self.dispatch_incoming_commands():
            pass
        while self.send_outgoing_commands():
            pass

    def dispatch_incoming_commands(self) -> bool:
        """Read from the transport and dispatch at most one command.

        Returns:
            Whether there may be more to dispatch.
        """
        # M1: map parsed packets to OperationResponse/EventData, answer
        # keep-alives, detect timeouts (disconnect_timeout).
        raise NotImplementedError

    def send_outgoing_commands(self) -> bool:
        """Flush queued commands to the transport.

        Returns:
            Whether more remain to be sent.
        """
        # M1: also emit a keep-alive when idle for keep_alive_interval.
        raise NotImplementedError

    # -- operations -------------------------------------------------------

    def send_operation(
        self, operation_code: int, parameters: Parameters, *, encrypt: bool = False
    ) -> bool:
        """Queue an operation; it goes out on the next ``send_outgoing_commands``.

        Returns:
            Whether the operation was queued.
        """
        # M1: build via PacketFactory.operation (encrypted when requested).
        raise NotImplementedError

    @staticmethod
    def _now() -> float:
        return time.monotonic()
