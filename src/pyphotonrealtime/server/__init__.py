"""A self-hosted Photon server: Name, Master and Game Server in one process.

Meant for tests and local play: official Photon clients (and
:class:`~pyphotonrealtime.RealtimeClient`) connect to it over TCP instead of
Photon Cloud. Rooms live in memory; every app id is accepted by default.

Extension points: subclass :class:`GameServer` (e.g. override
:meth:`GameServer.create_room`) or any :class:`RoleHandler` and pass it in
``PhotonServer(handlers={Role.GameServer: MyGameServer})``; relay other games
with ``passthrough=``; drop silent clients with ``idle_timeout=``.
:class:`PhotonProxy` sits between a client and a real server instead.
"""

from pyphotonrealtime.server.connection import Connection, Role, Session
from pyphotonrealtime.server.game_server import GameServer
from pyphotonrealtime.server.handler import RoleHandler
from pyphotonrealtime.server.master_server import MasterServer
from pyphotonrealtime.server.name_server import NameServer
from pyphotonrealtime.server.proxy import Direction, PhotonProxy, ProxySession
from pyphotonrealtime.server.relay import Relay
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
    "Connection",
    "Direction",
    "GameServer",
    "MasterServer",
    "NameServer",
    "PhotonProxy",
    "PhotonServer",
    "ProxySession",
    "Relay",
    "Role",
    "RoleHandler",
    "Session",
]
