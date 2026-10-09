"""Interop tests against the official C++ SDK's ``demo_loadBalancing``.

Our client and the real Photon C++ client meet in the same room, so each side
checks that the other's traffic is well-formed: the demo sees our join, we see
the demo's player and its events, and vice versa.

Every test runs twice: on Photon Cloud (needs ``PHOTON_APP_ID``) and on a
self-hosted ``PhotonServer``, which the demo's local build (TCP, our Name
Server) dials instead. Both also need Windows, MSVC and the Photon Windows C++
SDK (see ``sdk_demos/build.py``) and skip without them. Demos are built on
first use into ``build/``.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from pyphotonrealtime import AppSettings, ClientState, RealtimeClient
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.realtime import EnterRoomParams, RoomOptions
from pyphotonrealtime.realtime.callbacks import InRoomCallbacks, OnEventCallback
from pyphotonrealtime.server import PhotonServer

from .sdk_demos.build import DemoUnavailableError, build_demo
from .sdk_demos.process import TIMEOUT_SECONDS, DemoProcess

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pyphotonrealtime.peer import EventData
    from pyphotonrealtime.realtime.player import Player

APP_ID = os.environ.get("PHOTON_APP_ID", "")
LOCAL_APP_ID = "00000000-0000-0000-0000-000000000000"
"""Any app id will do for the self-hosted server."""
# demo_loadBalancing hardcodes both: it picks "eu" and sends app version "1.0".
DEMO_REGION = "eu"
DEMO_APP_VERSION = "1.0"
DEMO_NICK_NAME = "Windows"
DEMO_EVENT_CODE = 0

pytestmark = pytest.mark.e2e


@dataclass(slots=True)
class Target:
    """Where the demo and our client meet: Photon Cloud or a local server."""

    app_id: str
    local: bool = False
    settings: dict[str, Any] = field(default_factory=dict)
    """Extra ``AppSettings`` fields for our client."""
    env: dict[str, str] = field(default_factory=dict)
    """Extra environment for the demo."""


@pytest.fixture(params=["cloud", "local"])
def target(request: pytest.FixtureRequest) -> Iterator[Target]:
    if request.param == "cloud":
        if not APP_ID:
            pytest.skip("PHOTON_APP_ID not set")
        yield Target(APP_ID)
        return
    with PhotonServer(
        name_server_port=0,
        master_server_port=0,
        game_server_port=0,
        regions=(DEMO_REGION,),
    ) as server:
        yield Target(
            LOCAL_APP_ID,
            local=True,
            settings={
                "name_server": server.host,
                "name_server_port": server.name_server_port,
            },
            env={"PHOTON_SERVER_ADDRESS": server.name_server_address},
        )


class _Recorder(InRoomCallbacks, OnEventCallback):
    def __init__(self) -> None:
        self.events: list[EventData] = []
        self.entered: list[Player] = []
        self.left: list[Player] = []

    def on_event(self, event: EventData) -> None:
        self.events.append(event)

    def on_player_entered_room(self, player: Player) -> None:
        self.entered.append(player)

    def on_player_left_room(self, player: Player) -> None:
        self.left.append(player)


@pytest.fixture
def demo(target: Target) -> Iterator[DemoProcess]:
    try:
        exe = build_demo("demo_loadBalancing", local=target.local)
    except DemoUnavailableError as exc:
        pytest.skip(str(exc))
    process = DemoProcess(exe, {"PHOTON_APP_ID": target.app_id, **target.env})
    yield process
    process.close()


@pytest.fixture
def client(target: Target) -> Iterator[tuple[RealtimeClient, _Recorder]]:
    client = RealtimeClient()
    client.local_player.nick_name = "python"
    recorder = _Recorder()
    client.add_callback_target(recorder)
    client.connect_using_settings(
        AppSettings(
            app_id_realtime=target.app_id,
            app_version=DEMO_APP_VERSION,
            fixed_region=DEMO_REGION,
            **target.settings,
        )
    )
    _service_until(client, lambda: client.state == ClientState.ConnectedToMasterServer)
    yield client, recorder
    client.disconnect()


def _service_until(client: RealtimeClient, done: Callable[[], bool]) -> None:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while not done():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out in {client.state} ({client.disconnect_cause})")
        client.service()
        time.sleep(1 / 30)


def _demo_events(recorder: _Recorder) -> list[tuple[int, int]]:
    """(sender, count) of every event the demo sent us so far."""
    return [
        (
            int(event.parameters[ParameterKey.ActorNr].value),
            int(event.parameters[ParameterKey.Data].value),
        )
        for event in recorder.events
        if event.code == DEMO_EVENT_CODE
    ]


def test_join_room_created_by_demo(
    demo: DemoProcess, client: tuple[RealtimeClient, _Recorder]
) -> None:
    py, recorder = client
    demo.expect(r"connected to cluster", py)
    demo.press("1")  # create game
    room_name = demo.expect(r"room (\S+) has been created", py).group(1)

    assert py.op_join_room(EnterRoomParams(room_name=room_name))
    _service_until(py, lambda: py.state == ClientState.Joined)
    room = py.current_room
    assert room is not None
    assert room.max_players == 4  # what the demo's opCreateRoom asks for
    assert py.local_player.actor_number == 2
    assert room.players[1].nick_name == DEMO_NICK_NAME

    # The demo saw our join, including the nick name we sent...
    demo.expect(r"player 2 python has joined the game", py)
    # ...and its once-a-second counter events reach us, from actor 1.
    _service_until(py, lambda: len(_demo_events(recorder)) >= 2)
    events = _demo_events(recorder)
    assert {sender for sender, _ in events} == {1}
    counts = [count for _, count in events]
    assert counts == sorted(counts)
    assert len(set(counts)) == len(counts)

    assert py.op_leave_room()
    demo.expect(r"player 2 has left the game", py)


def test_demo_joins_room_created_by_us(
    demo: DemoProcess, client: tuple[RealtimeClient, _Recorder]
) -> None:
    py, recorder = client
    room_name = f"pyphotonrealtime-demo-{uuid.uuid4().hex[:8]}"
    assert py.op_create_room(
        EnterRoomParams(room_name=room_name, room_options=RoomOptions(max_players=4))
    )
    _service_until(py, lambda: py.state == ClientState.Joined)

    demo.expect(r"connected to cluster", py)
    demo.press("2")  # join random game
    joined = demo.expect(r"room (\S+) has been joined", py).group(1)
    if joined != room_name:
        pytest.skip(f"demo random-joined someone else's room {joined!r}")

    _service_until(py, lambda: bool(recorder.entered))
    assert recorder.entered[0].actor_number == 2
    assert recorder.entered[0].nick_name == DEMO_NICK_NAME
    _service_until(py, lambda: bool(_demo_events(recorder)))
    assert {sender for sender, _ in _demo_events(recorder)} == {2}

    demo.press("1")  # leave game
    _service_until(py, lambda: bool(recorder.left))
    assert recorder.left[0].actor_number == 2
