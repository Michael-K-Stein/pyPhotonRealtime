"""The official C++ SDK demos against a self-hosted ``PhotonServer``.

Each demo's local build (see ``sdk_demos/build.py``) connects over TCP to our
Name Server instead of Photon Cloud, so these run offline and test the server
side of our protocol code against the real Photon C++ client: two demos talk
through it, and must decode what the other sent.

Needs Windows, MSVC and the Photon Windows C++ SDK (no app id); skips without
them. Demos are built on first use into ``build/sdk_demos_local``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from pyphotonrealtime.server import PhotonServer

from .sdk_demos.build import DemoUnavailableError, build_demo
from .sdk_demos.process import DemoProcess

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

APP_ID = "00000000-0000-0000-0000-000000000000"
# Printed once the demo is authenticated on the Master Server (or in a room).
CONNECTED = {
    "demo_basics": r"connected to cluster default",
    "demo_typeSupport": r"room demo_photon_game has been entered",
    "demo_loadBalancing": r"connected to cluster default of region eu",
    "demo_particle": r"connected to cluster default of region eu",
}
# Bits of demo_typeSupport's event as the receiving demo prints it.
TYPE_SUPPORT_EVENT = (
    "(uchar)101=(int)10",
    '(JString)"testKey"=(float)[0.000000, 1.100000',
    "(int)1=(Dictionary<int, Object>){1=(int)[[10]]}",
    '(JString)"customType"=(SampleCustomType)<(uchar)12, (uchar)144>',
    "customTypeDictionaryEntry",
)

pytestmark = pytest.mark.e2e

type StartDemo = Callable[..., DemoProcess]


@pytest.fixture
def server() -> Iterator[PhotonServer]:
    with PhotonServer(
        name_server_port=0, master_server_port=0, game_server_port=0, regions=("eu",)
    ) as server:
        yield server


@pytest.fixture
def start_demo(server: PhotonServer) -> Iterator[StartDemo]:
    """Start a local build of a demo, connected to ``server``."""
    started: list[DemoProcess] = []

    def start(name: str, *, exit_key: str | None = None) -> DemoProcess:
        try:
            exe = build_demo(name, local=True)
        except DemoUnavailableError as exc:
            pytest.skip(str(exc))
        demo = DemoProcess(
            exe,
            {
                "PHOTON_APP_ID": APP_ID,
                "PHOTON_SERVER_ADDRESS": server.name_server_address,
            },
            exit_key=exit_key,
        )
        started.append(demo)
        return demo

    yield start
    for demo in started:
        demo.close()


@pytest.mark.parametrize("name", sorted(CONNECTED))
def test_demo_authenticates(
    name: str, start_demo: StartDemo, server: PhotonServer
) -> None:
    demo = start_demo(name, exit_key="0" if name == "demo_loadBalancing" else None)
    demo.expect(CONNECTED[name])
    # The token from the Name Server is all the Master Server got.
    assert server.sessions


def test_demo_basics_receives_its_own_events(start_demo: StartDemo) -> None:
    demo = start_demo("demo_basics")
    demo.expect(r"received event Nr\. 1")


def test_two_load_balancing_demos_exchange_events(start_demo: StartDemo) -> None:
    first = start_demo("demo_loadBalancing", exit_key="0")
    second = start_demo("demo_loadBalancing", exit_key="0")
    first.expect(CONNECTED["demo_loadBalancing"])
    second.expect(CONNECTED["demo_loadBalancing"])

    first.press("1")  # create game
    room = first.expect(r"room (\S+) has been created").group(1)
    second.press("2")  # join random game
    assert second.expect(r"room (\S+) has been joined").group(1) == room

    first.expect(r"player 2 Windows has joined the game")
    # Each prints the other's once-a-second counter events: R<count><-p<sender>.
    first.expect(r"R\d+<-p2 ")
    second.expect(r"R\d+<-p1 ")

    second.press("1")  # leave game
    first.expect(r"player 2 has left the game")


def test_type_support_event_decodes_on_both_sides(start_demo: StartDemo) -> None:
    first = start_demo("demo_typeSupport")
    first.expect(CONNECTED["demo_typeSupport"])
    second = start_demo("demo_typeSupport")
    second.expect(CONNECTED["demo_typeSupport"])
    first.expect(r"player 2 \S+ has joined the game")

    # Both join demo_photon_game and send the event on join, then regularly.
    for demo in (first, second):
        demo.expect(r"\(Hashtable\)\{")
        for part in TYPE_SUPPORT_EVENT:
            demo.expect(re.escape(part))
        assert "unsupported type code" not in demo.output


def test_particle_demos_see_each_others_colour_and_position(
    start_demo: StartDemo,
) -> None:
    first = start_demo("demo_particle")
    first.expect(r"room has been created")
    second = start_demo("demo_particle")
    second.expect(r"game room has been successfully joined")

    # The colour event is cached in the room, so the late joiner gets it too.
    second.expect(r"player 1 color: \d+")
    second.expect(r"player 1 pos: \d+, \d+")
    first.expect(r"player 2 color: \d+")
    first.expect(r"player 2 pos: \d+, \d+")
