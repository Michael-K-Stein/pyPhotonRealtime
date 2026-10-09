"""Pure-Python client for games running on Photon Realtime.

Layers, bottom to top:

* :mod:`pyphotonrealtime.protocol`  -- wire format: parameter (de)serialization
  (Protocol 1.6 and 1.8), custom types, packet framing, encryption primitives.
* :mod:`pyphotonrealtime.transport` -- transports: TCP, UDP, WebSocket(s).
* :mod:`pyphotonrealtime.peer`      -- ``PhotonPeer``: one connection to one server,
  operation/event/response queues, keep-alive, timeouts, encryption.
* :mod:`pyphotonrealtime.realtime`  -- ``RealtimeClient``: Name Server -> Master
  Server -> Game Server workflow, matchmaking, rooms, players, callbacks.

Beside them, :mod:`pyphotonrealtime.server` is a self-hosted Photon server
(``PhotonServer``) built on the same protocol layer.
"""

from pyphotonrealtime.peer import PeerState, PhotonPeer
from pyphotonrealtime.protocol.custom_types import register_type, unregister_type
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol
from pyphotonrealtime.realtime import (
    AppSettings,
    ClientState,
    RealtimeClient,
)
from pyphotonrealtime.transport import ConnectionProtocol

__version__ = "0.1.0.dev0"

__all__ = [
    "AppSettings",
    "ClientState",
    "ConnectionProtocol",
    "PeerState",
    "PhotonPeer",
    "RealtimeClient",
    "SerializationProtocol",
    "__version__",
    "register_type",
    "unregister_type",
]
