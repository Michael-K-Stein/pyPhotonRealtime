"""Transports that carry Photon packets: TCP, UDP and WebSockets."""

from pyphotonrealtime.transport.base import ConnectionProtocol, Transport
from pyphotonrealtime.transport.tcp import TcpTransport
from pyphotonrealtime.transport.udp import UdpTransport
from pyphotonrealtime.transport.websocket import (
    SecureWebSocketTransport,
    WebSocketTransport,
)

_TRANSPORTS: dict[ConnectionProtocol, type[Transport]] = {
    ConnectionProtocol.Udp: UdpTransport,
    ConnectionProtocol.Tcp: TcpTransport,
    ConnectionProtocol.WebSocket: WebSocketTransport,
    ConnectionProtocol.WebSocketSecure: SecureWebSocketTransport,
}


def create_transport(protocol: ConnectionProtocol) -> Transport:
    """Create an unconnected transport for ``protocol``.

    Returns:
        The transport.
    """
    return _TRANSPORTS[protocol]()


__all__ = [
    "ConnectionProtocol",
    "SecureWebSocketTransport",
    "TcpTransport",
    "Transport",
    "UdpTransport",
    "WebSocketTransport",
    "create_transport",
]
