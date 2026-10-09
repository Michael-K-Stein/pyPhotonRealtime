# pyPhotonRealtime

A pure-Python client for games running on [Photon Realtime](https://doc.photonengine.com/realtime-core/v6/getting-started/realtime-intro).
Connect, matchmake, join rooms, raise events and track properties and players,
all from Python.

> **Status: pre-alpha.** The wire layer works (it came from
> [prison-architect-server](https://github.com/Michael-K-Stein/prison-architect-server)).
> The client workflow is still scaffolding. See [ROADMAP.md](ROADMAP.md).

## Install

```bash
pip install -e ".[test,lint]"   # from a checkout; not on PyPI yet
```

Requires Python 3.12+. Fully type-annotated (`py.typed`), checked with `mypy --strict`.

## Intended usage

```python
import time
from pyphotonrealtime import AppSettings, RealtimeClient
from pyphotonrealtime.realtime import (
    ConnectionCallbacks,
    MatchmakingCallbacks,
    EnterRoomParams,
)


class Bot(ConnectionCallbacks, MatchmakingCallbacks):
    def __init__(self, client):
        self.client = client

    def on_connected_to_master(self):
        self.client.op_join_or_create_room(EnterRoomParams(room_name="lobby-1"))

    def on_joined_room(self):
        self.client.op_raise_event(1, {"hello": "world"})


client = RealtimeClient()
client.add_callback_target(Bot(client))
client.connect_using_settings(
    AppSettings(app_id_realtime="<your app id>", fixed_region="eu")
)  # protocol=ConnectionProtocol.Udp / WebSocketSecure for the other transports

while True:
    client.service()  # nothing happens on the wire without this
    time.sleep(1 / 30)
```

## Layout

| Package | Role |
|---|---|
| `pyphotonrealtime.protocol` | Wire format: typed parameters (Protocol 1.6 and 1.8), custom types, packet framing, DH/AES |
| `pyphotonrealtime.transport` | Transports: TCP, UDP (reliable/unreliable channels, fragmentation), WebSocket(s) |
| `pyphotonrealtime.peer` | `PhotonPeer`: one connection, the service loop, keep-alive, encryption |
| `pyphotonrealtime.realtime` | `RealtimeClient`: Name/Master/Game server workflow, rooms, players, callbacks |
| `pyphotonrealtime.server` | `PhotonServer`: a self-hosted Name, Master and Game Server over TCP |

## Protocols

Serialization defaults to Protocol 1.8, like the current SDKs; set
`client.peer.serialization_protocol = SerializationProtocol.V16` for 1.6. Clients on
either protocol and any transport can share a room. `SendOptions(reliable=False,
channel=1)` only changes anything over UDP. Over WSS, TLS replaces payload encryption,
as in the SDKs.

Your own classes can travel as Photon custom types:

```python
import struct
from pyphotonrealtime import register_type

register_type(
    Vector2,
    code=ord("W"),
    serialize=lambda v: struct.pack(">ff", v.x, v.y),
    deserialize=lambda data: Vector2(*struct.unpack(">ff", data)),
)
client.op_raise_event(1, {"at": Vector2(1, 2)})  # arrives as a Vector2
```

## Self-hosted server

`PhotonServer` runs a Name, Master and Game Server in one process, over TCP, with
rooms in memory: a local stand-in for Photon Cloud, for tests and offline play.
Official Photon clients connect to it too (the C++ SDK demos do, see Testing).

```python
from pyphotonrealtime.server import PhotonServer

with PhotonServer() as server:  # Name Server on 127.0.0.1:4533, any app id
    client.connect_using_settings(
        AppSettings(
            app_id_realtime="<any app id>",
            name_server=server.host,
            name_server_port=server.name_server_port,
            fixed_region="local",
        )
    )
```

It covers authentication (including AuthOnce), regions, lobbies and room lists,
create/join/random join, events (receivers, targets, interest groups, the room
cache), room and player properties with compare-and-swap, master client handover
and player TTLs. Not yet: UDP and WebSockets, SQL lobby filters, plugins and
WebHooks.

## Development

```bash
python -m ruff --version          # must match the pin in pyproject.toml
python -m ruff check .            # select = ["ALL"], see ruff.toml
python -m ruff format --check .
python -m mypy                    # strict
python -m pytest -q               # unit tests (e2e excluded)
PHOTON_APP_ID=... python -m pytest -m e2e   # live Photon Cloud, opt-in
python -m pytest -m e2e tests/e2e/test_sdk_demos_local.py  # C++ demos vs PhotonServer
```

## Testing

Unit tests check that the protocol layer agrees with itself (round trips, framing).
That doesn't prove it agrees with Photon. The `e2e` tests in `tests/e2e/` do that
against the real Photon Cloud. They need a Realtime app id in `PHOTON_APP_ID`, and
CI runs them nightly and on manual dispatch, never on PRs.

`tests/e2e/test_sdk_demos*.py` run the official Photon C++ SDK's demos (Windows,
MSVC and the SDK in `sdks/` or `$PHOTON_CPP_SDK`; they skip otherwise). Against
Photon Cloud, our client and `demo_loadBalancing` share a room. Against
`PhotonServer` they need no app id: the demos' local builds connect over TCP to our
Name Server, and pairs of demos (`demo_loadBalancing`, `demo_typeSupport`,
`demo_particle`) talk to each other through it, which tests the server side of the
protocol against the real C++ client.
```
