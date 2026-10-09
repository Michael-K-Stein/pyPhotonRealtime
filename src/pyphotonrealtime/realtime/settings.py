from dataclasses import dataclass
from typing import Optional

DEFAULT_NAME_SERVER = "ns.photonengine.io"
DEFAULT_NAME_SERVER_PORT_TCP = 4533


@dataclass
class AppSettings:
    """Equivalent of the SDK's ``AppSettings``."""

    app_id_realtime: str
    app_version: str = ""
    fixed_region: Optional[str] = None
    # Skip the Name Server and connect straight to a Master Server
    # (self-hosted / local servers such as prison-architect-server).
    use_name_server: bool = True
    server: Optional[str] = None
    port: int = 0
    name_server: str = DEFAULT_NAME_SERVER
    name_server_port: int = DEFAULT_NAME_SERVER_PORT_TCP
    enable_lobby_statistics: bool = False
