"""Pure-Python client for games running on Photon Realtime.

Layers, bottom to top:

* :mod:`pyphotonrealtime.protocol`  -- wire format: parameter (de)serialization,
  packet framing, encryption primitives.
* :mod:`pyphotonrealtime.transport` -- byte transports (TCP now; UDP/WebSocket later).
* :mod:`pyphotonrealtime.peer`      -- ``PhotonPeer``: one connection to one server,
  operation/event/response queues, keep-alive, timeouts, encryption.
* :mod:`pyphotonrealtime.realtime`  -- ``RealtimeClient``: Name Server -> Master
  Server -> Game Server workflow, matchmaking, rooms, players, callbacks.
"""

from pyphotonrealtime.peer import PeerState, PhotonPeer
from pyphotonrealtime.realtime import (
    AppSettings,
    ClientState,
    RealtimeClient,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "AppSettings",
    "ClientState",
    "PeerState",
    "PhotonPeer",
    "RealtimeClient",
    "__version__",
]
