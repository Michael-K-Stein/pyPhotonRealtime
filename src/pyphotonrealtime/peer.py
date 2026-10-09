"""``PhotonPeer``: a single connection to a single Photon server.

Mirrors the official SDKs' service loop: nothing happens on the network unless
the application calls :meth:`PhotonPeer.service` (or the two halves,
:meth:`dispatch_incoming_commands` and :meth:`send_outgoing_commands`) regularly,
typically 10-50 times per second, from a single thread.

Callbacks to the :class:`PeerListener` are only ever invoked from inside
``dispatch_incoming_commands``, i.e. on the thread that calls ``service``.
"""

import time
from collections import deque
from enum import Enum, auto
from typing import Any, Deque, Dict, Optional, Protocol

from pyphotonrealtime.protocol.packet.base import PhotonPacket
from pyphotonrealtime.protocol.packet.packet_stream import PhotonStreamParser
from pyphotonrealtime.protocol.param.base import ParameterBase
from pyphotonrealtime.transport import TcpTransport, Transport

Parameters = Dict[int, ParameterBase[Any]]


class PeerState(Enum):
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


class OperationResponse:
    def __init__(
        self,
        operation_code: int,
        return_code: int,
        debug_message: Optional[str],
        parameters: Parameters,
    ) -> None:
        self.operation_code = operation_code
        self.return_code = return_code
        self.debug_message = debug_message
        self.parameters = parameters

    def __repr__(self) -> str:
        return (
            f"OperationResponse(op={self.operation_code}, rc={self.return_code}, "
            f"msg={self.debug_message!r}, params={self.parameters!r})"
        )


class EventData:
    def __init__(self, code: int, parameters: Parameters) -> None:
        self.code = code
        self.parameters = parameters

    def __repr__(self) -> str:
        return f"EventData(code={self.code}, params={self.parameters!r})"


class PeerListener(Protocol):
    """Equivalent of ``IPhotonPeerListener``."""

    def on_status_changed(self, status: StatusCode) -> None: ...

    def on_operation_response(self, response: OperationResponse) -> None: ...

    def on_event(self, event: EventData) -> None: ...


class PhotonPeer:
    def __init__(
        self,
        listener: PeerListener,
        transport: Optional[Transport] = None,
        *,
        disconnect_timeout: float = 10.0,
        keep_alive_interval: float = 2.0,
    ) -> None:
        self.listener = listener
        self.transport = transport if transport is not None else TcpTransport()
        self.disconnect_timeout = disconnect_timeout
        self.keep_alive_interval = keep_alive_interval

        self.state = PeerState.Disconnected
        self.server_address: Optional[str] = None
        self.app_id: Optional[str] = None

        self._parser = PhotonStreamParser()
        self._outgoing: Deque[PhotonPacket] = deque()
        self._incoming: Deque[PhotonPacket] = deque()
        self._aes_key: Optional[bytes] = None
        self._last_receive = 0.0
        self._last_send = 0.0

    # -- connection -------------------------------------------------------

    def connect(self, server_address: str, app_id: str) -> bool:
        """Start connecting to ``host:port``. Result arrives as a status callback.

        TODO: send ``InitRequestPacket`` (app id, protocol version) and move to
        ``Connected`` on ``InitResponse``.
        """
        raise NotImplementedError

    def disconnect(self) -> None:
        """TODO: send a DisconnectMessage, then close the transport."""
        raise NotImplementedError

    def establish_encryption(self) -> bool:
        """TODO: Diffie-Hellman exchange via ``InitEncryptionRequest``."""
        raise NotImplementedError

    @property
    def is_encryption_available(self) -> bool:
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

        Returns True if there may be more to dispatch.

        TODO: map parsed packets to ``OperationResponse``/``EventData``, answer
        keep-alives, detect timeouts (``disconnect_timeout``).
        """
        raise NotImplementedError

    def send_outgoing_commands(self) -> bool:
        """Flush queued commands to the transport. Returns True if more remain.

        TODO: also emit a keep-alive when idle for ``keep_alive_interval``.
        """
        raise NotImplementedError

    # -- operations -------------------------------------------------------

    def send_operation(
        self, operation_code: int, parameters: Parameters, *, encrypt: bool = False
    ) -> bool:
        """Queue an operation. It is sent on the next ``send_outgoing_commands``.

        TODO: build via ``PacketFactory.operation`` (encrypted when requested).
        """
        raise NotImplementedError

    # -- stats ------------------------------------------------------------

    @staticmethod
    def _now() -> float:
        return time.monotonic()
