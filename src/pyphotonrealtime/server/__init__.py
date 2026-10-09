"""A self-hosted Photon server: Name, Master and Game Server in one process.

Meant for tests and local play: official Photon clients (and
:class:`~pyphotonrealtime.RealtimeClient`) connect to it over TCP instead of
Photon Cloud. Rooms live in memory; every app id is accepted by default.
"""

from pyphotonrealtime.server.server import (
    DEFAULT_GAME_SERVER_PORT,
    DEFAULT_MASTER_SERVER_PORT,
    DEFAULT_NAME_SERVER_PORT,
    PhotonServer,
)

__all__ = [
    "DEFAULT_GAME_SERVER_PORT",
    "DEFAULT_MASTER_SERVER_PORT",
    "DEFAULT_NAME_SERVER_PORT",
    "PhotonServer",
]
