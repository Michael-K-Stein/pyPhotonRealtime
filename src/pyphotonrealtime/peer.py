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
from typing import TYPE_CHECKING, Any, Protocol, cast

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.packet.disconnect import DisconnectMessagePacket
from pyphotonrealtime.protocol.packet.factory import PacketFactory
from pyphotonrealtime.protocol.packet.init import InitRequestPacket, InitResponsePacket
from pyphotonrealtime.protocol.packet.keep_alive import (
    PhotonKeepAliveRequest,
    PhotonKeepAliveResponse,
)
from pyphotonrealtime.protocol.packet.key_exchange import InitEncryptionResponse
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.packet.packet_stream import PhotonStreamParser
from pyphotonrealtime.protocol.photon_enc import build_dh_request, process_dh_response
from pyphotonrealtime.transport import TcpTransport

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.enum_lookups import CommandParams
    from pyphotonrealtime.protocol.operation_code import OperationCode
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


@dataclass(slots=True)
class TrafficStats:
    """Byte and packet counters since the last ``connect``."""

    bytes_in: int = 0
    bytes_out: int = 0
    packets_in: int = 0
    packets_out: int = 0


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

        self.traffic_stats = TrafficStats()
        self.round_trip_time = 0.0
        """Smoothed keep-alive round-trip time in milliseconds."""
        self.round_trip_time_variance = 0.0
        self.last_round_trip_time: int | None = None
        """Most recent keep-alive round-trip time in milliseconds."""

        self._parser = PhotonStreamParser()
        self._outgoing: deque[PhotonPacket] = deque()
        self._incoming: deque[PhotonPacket | StatusCode] = deque()
        self._aes_key: bytes | None = None
        self._dh_private_key: int | None = None
        self._epoch = self._now()
        self._last_receive = 0.0
        self._last_send = 0.0

    # -- connection -------------------------------------------------------

    def connect(self, server_address: str, app_id: str) -> bool:
        """Connect to ``host:port`` and send the init request.

        The TCP connect itself blocks (up to ``disconnect_timeout``). The
        outcome arrives as ``StatusCode.Connect`` once the server acknowledged
        the init, or as ``StatusCode.ExceptionOnConnect``.

        Returns:
            Whether the connection attempt was started.

        Raises:
            ValueError: ``server_address`` is not ``host:port``.
        """
        if self.state != PeerState.Disconnected:
            return False
        host, _, port = server_address.rpartition(":")
        if not host or not port.isdigit():
            msg = f"expected host:port, got {server_address!r}"
            raise ValueError(msg)

        self._reset()
        self.server_address = server_address
        self.app_id = app_id
        try:
            self.transport.connect(host.strip("[]"), int(port), self.disconnect_timeout)
        except OSError:
            self._incoming.append(StatusCode.ExceptionOnConnect)
            return False

        self.state = PeerState.Connecting
        self._last_receive = self._last_send = self._now()
        self._outgoing.append(InitRequestPacket(app_id=app_id.replace("-", "")))
        return True

    def disconnect(self) -> None:
        """Close the connection; ``StatusCode.Disconnect`` follows on dispatch."""
        if self.state == PeerState.Disconnected:
            return
        self._close(StatusCode.Disconnect)

    def establish_encryption(self) -> bool:
        """Start a Diffie-Hellman key exchange (``InitEncryptionRequest``).

        The outcome arrives as ``StatusCode.EncryptionEstablished`` or
        ``StatusCode.EncryptionFailedToEstablish``.

        Returns:
            Whether the exchange was started.
        """
        if self.state != PeerState.Connected or self._dh_private_key is not None:
            return False
        self._dh_private_key, request = build_dh_request()
        self._outgoing.append(request)
        return True

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
        self._receive()
        if not self._incoming:
            return False
        item = self._incoming.popleft()
        if isinstance(item, StatusCode):
            self.listener.on_status_changed(item)
        else:
            self._dispatch_packet(item)
        return bool(self._incoming)

    def send_outgoing_commands(self) -> bool:
        """Send one queued command, or a keep-alive when idle.

        Returns:
            Whether more remain to be sent.
        """
        if self.state not in {PeerState.Connecting, PeerState.Connected}:
            return False
        now = self._now()
        if (
            self.state == PeerState.Connected
            and not self._outgoing
            and now - self._last_send >= self.keep_alive_interval
        ):
            self._outgoing.append(PhotonKeepAliveRequest(self._client_time()))
        try:
            if self._outgoing:
                data = self._outgoing.popleft().serialize()
                self.transport.send(data)
                self.traffic_stats.bytes_out += len(data)
                self.traffic_stats.packets_out += 1
                self._last_send = now
            else:
                self.transport.flush()
        except OSError:
            self._close(StatusCode.Exception)
            return False
        return bool(self._outgoing)

    # -- operations -------------------------------------------------------

    def send_operation(
        self, operation_code: int, parameters: Parameters, *, encrypt: bool = False
    ) -> bool:
        """Queue an operation; it goes out on the next ``send_outgoing_commands``.

        Returns:
            Whether the operation was queued. ``False`` when not connected, or
            when ``encrypt`` is requested before encryption is established.
        """
        if self.state != PeerState.Connected:
            return False
        if encrypt and self._aes_key is None:
            return False
        packet = PacketFactory.operation(
            CommandCode.EncryptedOperation if encrypt else CommandCode.Operation,
            operation=cast("OperationCode", operation_code),
            params=cast("CommandParams", dict(parameters)),
        )
        if self._aes_key is not None:
            packet.set_aes_key(self._aes_key)
        self._outgoing.append(packet)
        return True

    # -- internals --------------------------------------------------------

    def _reset(self) -> None:
        self._parser = PhotonStreamParser()
        self._outgoing.clear()
        self._aes_key = None
        self._dh_private_key = None
        self.traffic_stats = TrafficStats()
        self.round_trip_time = 0.0
        self.round_trip_time_variance = 0.0
        self.last_round_trip_time = None

    def _close(self, status: StatusCode) -> None:
        self.transport.close()
        self._outgoing.clear()
        self.state = PeerState.Disconnected
        self._incoming.append(status)

    def _receive(self) -> None:
        if self.state not in {PeerState.Connecting, PeerState.Connected}:
            return
        now = self._now()
        data = self.transport.receive()
        if data:
            self._last_receive = now
            self.traffic_stats.bytes_in += len(data)
            self._parser.feed(data)
            try:
                for packet in self._parser.parse(
                    expect_responses=True, aes_key=self._aes_key
                ):
                    self.traffic_stats.packets_in += 1
                    self._incoming.append(packet)
            except (ValueError, TypeError):
                self._close(StatusCode.Exception)
                return
        if not self.transport.connected:
            self._close(StatusCode.DisconnectByServer)
        elif now - self._last_receive > self.disconnect_timeout:
            self._close(StatusCode.TimeoutDisconnect)

    def _dispatch_packet(self, packet: PhotonPacket) -> None:
        if isinstance(packet, PhotonKeepAliveResponse):
            self._update_round_trip_time(packet.get_client_time())
        elif isinstance(packet, InitResponsePacket):
            if self.state == PeerState.Connecting:
                self.state = PeerState.Connected
                self.listener.on_status_changed(StatusCode.Connect)
        elif isinstance(packet, InitEncryptionResponse):
            self._finish_key_exchange(packet)
        elif isinstance(packet, DisconnectMessagePacket):
            self._close(StatusCode.DisconnectByServer)
        elif isinstance(packet, PhotonOperationPacket):
            self._dispatch_operation(packet)

    def _dispatch_operation(self, packet: PhotonOperationPacket) -> None:
        payload = packet.get_payload()
        parameters = cast("Parameters", payload.params)
        if packet.get_header().is_response():
            self.listener.on_operation_response(
                OperationResponse(
                    operation_code=int(payload.operation_code),
                    return_code=payload.get_return_code() or 0,
                    debug_message=payload.get_debug_message(),
                    parameters=parameters,
                )
            )
        elif packet.get_header().get_command_code() in {
            CommandCode.Event,
            CommandCode.EncryptedEvent,
        }:
            self.listener.on_event(
                EventData(code=int(payload.operation_code), parameters=parameters)
            )

    def _finish_key_exchange(self, response: InitEncryptionResponse) -> None:
        private_key, self._dh_private_key = self._dh_private_key, None
        if private_key is None:
            return  # Unsolicited; ignore it.
        if not response.get_public_key():
            self.listener.on_status_changed(StatusCode.EncryptionFailedToEstablish)
            return
        self._aes_key = process_dh_response(private_key, response)
        self.listener.on_status_changed(StatusCode.EncryptionEstablished)

    def _update_round_trip_time(self, client_time: int) -> None:
        # Same smoothing as the official SDKs (UpdateRoundTripTimeAndVariance).
        rtt = (self._client_time() - client_time) & 0xFFFFFFFF
        if self.last_round_trip_time is None:
            self.round_trip_time = float(rtt)
        else:
            delta = rtt - self.round_trip_time
            self.round_trip_time += delta / 8
            self.round_trip_time_variance += (
                abs(delta) - self.round_trip_time_variance
            ) / 4
        self.last_round_trip_time = rtt

    def _client_time(self) -> int:
        """Milliseconds since this peer was created, as a 32-bit timestamp."""
        return int((self._now() - self._epoch) * 1000) & 0xFFFFFFFF

    @staticmethod
    def _now() -> float:
        return time.monotonic()
