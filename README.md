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

Requires Python 3.10+.

## Intended usage

```python
import time
from pyphotonrealtime import AppSettings, RealtimeClient
from pyphotonrealtime.realtime import ConnectionCallbacks, MatchmakingCallbacks, EnterRoomParams


class Bot(ConnectionCallbacks, MatchmakingCallbacks):
    def __init__(self, client):
        self.client = client

    def on_connected_to_master(self):
        self.client.op_join_or_create_room(EnterRoomParams(room_name="lobby-1"))

    def on_joined_room(self):
        self.client.op_raise_event(1, {"hello": "world"})


client = RealtimeClient()
client.add_callback_target(Bot(client))
client.connect_using_settings(AppSettings(app_id_realtime="<your app id>", fixed_region="eu"))

while True:
    client.service()        # nothing happens on the wire without this
    time.sleep(1 / 30)
```

## Layout

| Package | Role |
|---|---|
| `pyphotonrealtime.protocol` | Wire format: typed parameters, packet framing, DH/AES |
| `pyphotonrealtime.transport` | Byte transports (TCP; UDP/WebSocket planned) |
| `pyphotonrealtime.peer` | `PhotonPeer`: one connection, the service loop, keep-alive, encryption |
| `pyphotonrealtime.realtime` | `RealtimeClient`: Name/Master/Game server workflow, rooms, players, callbacks |

## Development

```bash
python -m ruff --version          # must match the pin in pyproject.toml
python -m ruff check --select E9,F63,F7,F82 .
python -m ruff format --check .
python -m pytest -q
```
