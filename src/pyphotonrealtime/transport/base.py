"""Abstract byte transport."""

from abc import ABC, abstractmethod
from enum import IntEnum
from typing import ClassVar


class ConnectionProtocol(IntEnum):
    """Network protocol of a transport, with the SDKs' numeric values."""

    Udp = 0
    Tcp = 1
    WebSocket = 4
    WebSocketSecure = 5


class Transport(ABC):
    """A non-blocking pipe to a single Photon server.

    The upper edge always speaks Photon's TCP framing: ``PhotonPeer`` sends
    and receives TCP packets (``0xFB`` + length + channel + reliability flag +
    message, and ``0xF0`` pings) and feeds what ``receive`` returns into a
    ``PhotonStreamParser``. Transports for other protocols translate: they
    unwrap each packet into one of their own messages and wrap what arrives
    back into TCP packets.
    """

    protocol: ClassVar[ConnectionProtocol] = ConnectionProtocol.Tcp
    """What the Name Server is told this client uses (``ExpectedProtocol``)."""
    ping_with_operation: ClassVar[bool] = False
    """Keep alive with the internal ping operation instead of ``0xF0`` packets."""
    secure: ClassVar[bool] = False
    """Encrypted by the transport (TLS); payload encryption is then skipped."""
    path = ""
    """Path of the server address (``/Master`` in ``wss://host:443/Master``);
    set by the peer before ``connect``. Only WebSockets use it."""

    @abstractmethod
    def connect(self, host: str, port: int, timeout: float) -> None:
        """Open the connection. Blocking; raises ``OSError`` on failure."""

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Queue ``data`` for sending. Must not block on a slow peer."""

    def flush(self) -> bool:
        """Write as much queued data as the connection accepts right now.

        Returns:
            Whether data is still waiting to be written.
        """
        return False

    @abstractmethod
    def receive(self) -> bytes:
        """Return whatever bytes are available right now (``b""`` if none)."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection. Idempotent."""

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Whether the connection is currently open."""
