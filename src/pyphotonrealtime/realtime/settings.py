"""Connection settings."""

from dataclasses import dataclass

from pyphotonrealtime.realtime.authentication import AuthMode
from pyphotonrealtime.transport.base import ConnectionProtocol

DEFAULT_NAME_SERVER = "ns.photonengine.io"
DEFAULT_NAME_SERVER_PORT_TCP = 4533
DEFAULT_MASTER_SERVER_PORT_TCP = 4530

NAME_SERVER_PORTS = {
    ConnectionProtocol.Udp: 5058,
    ConnectionProtocol.Tcp: DEFAULT_NAME_SERVER_PORT_TCP,
    ConnectionProtocol.WebSocket: 9093,
    ConnectionProtocol.WebSocketSecure: 19093,
}
MASTER_SERVER_PORTS = {
    ConnectionProtocol.Udp: 5055,
    ConnectionProtocol.Tcp: DEFAULT_MASTER_SERVER_PORT_TCP,
    ConnectionProtocol.WebSocket: 9090,
    ConnectionProtocol.WebSocketSecure: 19090,
}


@dataclass(slots=True, kw_only=True)
class AppSettings:
    """Equivalent of the SDK's ``AppSettings``."""

    app_id_realtime: str
    app_version: str = ""
    fixed_region: str | None = None
    # Skip the Name Server and connect straight to a Master Server
    # (self-hosted / local servers such as prison-architect-server).
    use_name_server: bool = True
    server: str | None = None
    """Master Server host when ``use_name_server`` is False."""
    port: int = 0
    """Master Server port; 0 means the protocol's default (4530 for TCP)."""
    protocol: ConnectionProtocol = ConnectionProtocol.Tcp
    """Transport the client creates; ignored when it was given one."""
    auth_mode: AuthMode = AuthMode.Auth
    name_server: str = DEFAULT_NAME_SERVER
    name_server_port: int = 0
    """0 means the protocol's default (4533 for TCP)."""
    enable_lobby_statistics: bool = False
