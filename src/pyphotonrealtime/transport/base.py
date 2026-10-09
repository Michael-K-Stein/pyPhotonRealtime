"""Abstract byte transport."""

from abc import ABC, abstractmethod


class Transport(ABC):
    """A non-blocking byte pipe to a single Photon server.

    Transports know nothing about Photon framing; ``PhotonPeer`` feeds what
    ``receive`` returns into a ``PhotonStreamParser``.
    """

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
