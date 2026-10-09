"""Interop tests against the official C++ SDK's ``demo_loadBalancing``.

Our client and the real Photon C++ client meet in the same room on Photon
Cloud, so each side checks that the other's traffic is well-formed: the demo
sees our join, we see the demo's player and its events, and vice versa.

Same opt-in as the other e2e tests (``PHOTON_APP_ID``), plus Windows, MSVC
and the Photon Windows C++ SDK (see ``sdk_demos/build.py``); they skip when
any of those is missing. The demo is built on first use into ``build/``.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
import uuid
from typing import TYPE_CHECKING

import pytest

from pyphotonrealtime import AppSettings, ClientState, RealtimeClient
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.realtime import EnterRoomParams, RoomOptions
from pyphotonrealtime.realtime.callbacks import InRoomCallbacks, OnEventCallback

from .sdk_demos.build import DemoUnavailableError, build_demo

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from pyphotonrealtime.peer import EventData
    from pyphotonrealtime.realtime.player import Player

APP_ID = os.environ.get("PHOTON_APP_ID", "")
# demo_loadBalancing hardcodes both: it picks "eu" and sends app version "1.0".
DEMO_REGION = "eu"
DEMO_APP_VERSION = "1.0"
DEMO_NICK_NAME = "Windows"
DEMO_EVENT_CODE = 0
TIMEOUT_SECONDS = 20.0

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not APP_ID, reason="PHOTON_APP_ID not set"),
]


class DemoProcess:
    """The demo running as a subprocess, driven by its menu keys."""

    def __init__(self, exe: Path) -> None:
        self._proc = subprocess.Popen(  # noqa: S603 -- our own build output
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PHOTON_APP_ID": APP_ID},
        )
        self.output = ""
        self._chunks: queue.Queue[bytes] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        while chunk := os.read(self._proc.stdout.fileno(), 4096):
            self._chunks.put(chunk)

    def press(self, key: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(f"{key}\n".encode())
        self._proc.stdin.flush()

    def expect(
        self, pattern: str, client: RealtimeClient | None = None
    ) -> re.Match[str]:
        """Wait for ``pattern`` in the demo's output, servicing ``client``.

        Returns:
            The first match.
        """
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while not (match := re.search(pattern, self.output)):
            if time.monotonic() > deadline:
                pytest.fail(f"demo never printed {pattern!r}; output:\n{self.output}")
            if client is not None:
                client.service()
            try:
                chunk = self._chunks.get(timeout=1 / 30)
            except queue.Empty:
                continue
            self.output += chunk.decode("utf-8", "replace")
        return match

    def close(self) -> None:
        if self._proc.poll() is None:
            self.press("0")
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()


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


@pytest.fixture(scope="module")
def demo_exe() -> Path:
    try:
        return build_demo("demo_loadBalancing")
    except DemoUnavailableError as exc:
        pytest.skip(str(exc))


@pytest.fixture
def demo(demo_exe: Path) -> Iterator[DemoProcess]:
    process = DemoProcess(demo_exe)
    yield process
    process.close()


@pytest.fixture
def client() -> Iterator[tuple[RealtimeClient, _Recorder]]:
    client = RealtimeClient()
    client.local_player.nick_name = "python"
    recorder = _Recorder()
    client.add_callback_target(recorder)
    client.connect_using_settings(
        AppSettings(
            app_id_realtime=APP_ID,
            app_version=DEMO_APP_VERSION,
            fixed_region=DEMO_REGION,
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
