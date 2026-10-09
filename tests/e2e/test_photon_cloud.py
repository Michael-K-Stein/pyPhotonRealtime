"""End-to-end tests against the real Photon Cloud.

Opt-in: they only run with ``PHOTON_APP_ID`` set (a Realtime app id from the
Photon dashboard) and are excluded from the default run by the ``e2e`` marker::

    PHOTON_APP_ID=... python -m pytest -m e2e

They verify the wire protocol against live servers, which unit tests (which
only check self-consistency) can't do.
"""

from __future__ import annotations

import os
import struct
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

import pytest

from pyphotonrealtime import AppSettings, ClientState, PhotonPeer, RealtimeClient
from pyphotonrealtime.peer import PeerState, StatusCode
from pyphotonrealtime.protocol.custom_types import register_type, unregister_type
from pyphotonrealtime.protocol.event_code import CUSTOM_EVENT_CODE_MAX
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol
from pyphotonrealtime.realtime import (
    AuthMode,
    EnterRoomParams,
    InRoomCallbacks,
    OnEventCallback,
    RaiseEventArgs,
    RoomOptions,
    SendOptions,
)
from pyphotonrealtime.transport import ConnectionProtocol, create_transport

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pyphotonrealtime.peer import EventData, OperationResponse
    from pyphotonrealtime.realtime import Player

APP_ID = os.environ.get("PHOTON_APP_ID", "")
REGION = os.environ.get("PHOTON_REGION", "eu")
# Override when the hosts file redirects the Name Server (e.g. to a local PA server).
NAME_SERVER = os.environ.get("PHOTON_NAME_SERVER", "ns.photonengine.io:4533")
TIMEOUT_SECONDS = 15.0

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not APP_ID, reason="PHOTON_APP_ID not set"),
]

PROTOCOLS = [
    pytest.param(ConnectionProtocol.Tcp, id="tcp"),
    pytest.param(ConnectionProtocol.Udp, id="udp"),
    pytest.param(ConnectionProtocol.WebSocketSecure, id="wss"),
]
NAME_SERVER_PORTS = {
    ConnectionProtocol.Udp: 5058,
    ConnectionProtocol.Tcp: 4533,
    ConnectionProtocol.WebSocket: 9093,
    ConnectionProtocol.WebSocketSecure: 19093,
}


def _service_until(client: RealtimeClient, state: ClientState) -> None:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while client.state != state:
        if time.monotonic() > deadline:
            pytest.fail(
                f"timed out in {client.state} ({client.disconnect_cause}) "
                f"waiting for {state}"
            )
        client.service()
        time.sleep(1 / 30)


@pytest.mark.parametrize(
    "protocol", [*PROTOCOLS, pytest.param(ConnectionProtocol.WebSocket, id="ws")]
)
@pytest.mark.parametrize(
    "serialization",
    [
        pytest.param(SerializationProtocol.V16, id="gp16"),
        pytest.param(SerializationProtocol.V18, id="gp18"),
    ],
)
def test_connect_to_master(
    protocol: ConnectionProtocol, serialization: SerializationProtocol
) -> None:
    client = RealtimeClient()
    client.peer.serialization_protocol = serialization
    client.connect_using_settings(
        AppSettings(app_id_realtime=APP_ID, fixed_region=REGION, protocol=protocol)
    )
    _service_until(client, ClientState.ConnectedToMasterServer)
    assert client.user_id  # Photon assigns one when none is sent.
    assert client.protocol == protocol
    client.disconnect()
    _service_until(client, ClientState.Disconnected)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_connect_to_best_region(protocol: ConnectionProtocol) -> None:
    client = RealtimeClient()
    client.connect_using_settings(
        AppSettings(app_id_realtime=APP_ID, protocol=protocol)
    )
    _service_until(client, ClientState.ConnectedToMasterServer)
    assert client.cloud_region
    assert any(region.ping is not None for region in client.regions)
    client.disconnect()


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_auth_once_to_master(protocol: ConnectionProtocol) -> None:
    client = RealtimeClient()
    client.connect_using_settings(
        AppSettings(
            app_id_realtime=APP_ID,
            fixed_region=REGION,
            auth_mode=AuthMode.AuthOnce,
            protocol=protocol,
        )
    )
    _service_until(client, ClientState.ConnectedToMasterServer)
    client.disconnect()


def _connected_client(
    protocol: ConnectionProtocol = ConnectionProtocol.Tcp,
    serialization: SerializationProtocol = SerializationProtocol.V18,
) -> RealtimeClient:
    client = RealtimeClient()
    client.peer.serialization_protocol = serialization
    client.connect_using_settings(
        AppSettings(app_id_realtime=APP_ID, fixed_region=REGION, protocol=protocol)
    )
    _service_until(client, ClientState.ConnectedToMasterServer)
    return client


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_create_join_and_leave_room(protocol: ConnectionProtocol) -> None:
    room_name = f"pyphotonrealtime-e2e-{uuid.uuid4().hex[:8]}"
    host = _connected_client(protocol)
    host.local_player.nick_name = "host"
    assert host.op_create_room(
        EnterRoomParams(
            room_name=room_name,
            room_options=RoomOptions(
                max_players=2, custom_room_properties={"mode": "e2e"}
            ),
        )
    )
    _service_until(host, ClientState.Joined)
    assert host.current_room is not None
    assert host.current_room.custom_properties == {"mode": "e2e"}
    assert host.local_player.actor_number == 1

    guest = _connected_client(protocol)
    assert guest.op_join_room(EnterRoomParams(room_name=room_name))
    _service_until(guest, ClientState.Joined)
    room = guest.current_room
    assert room is not None
    assert room.max_players == 2
    assert guest.local_player.actor_number == 2
    assert room.players[1].nick_name == "host"

    assert guest.op_leave_room()
    _service_until(guest, ClientState.ConnectedToMasterServer)
    assert guest.op_join_lobby()
    _service_until(guest, ClientState.JoinedLobby)
    guest.disconnect()
    host.disconnect()


class _InRoomRecorder(InRoomCallbacks, OnEventCallback):
    def __init__(self) -> None:
        self.events: list[EventData] = []
        self.entered: list[int] = []
        self.left: list[int] = []
        self.room_changes: list[dict[Any, Any]] = []
        self.player_changes: list[tuple[int, dict[Any, Any]]] = []
        self.masters: list[int] = []

    @override
    def on_event(self, event: EventData) -> None:
        if event.code < CUSTOM_EVENT_CODE_MAX:
            self.events.append(event)

    @override
    def on_player_entered_room(self, player: Player) -> None:
        self.entered.append(player.actor_number)

    @override
    def on_player_left_room(self, player: Player) -> None:
        self.left.append(player.actor_number)

    @override
    def on_room_properties_update(self, changed: dict[Any, Any]) -> None:
        self.room_changes.append(changed)

    @override
    def on_player_properties_update(
        self, player: Player, changed: dict[Any, Any]
    ) -> None:
        self.player_changes.append((player.actor_number, changed))

    @override
    def on_master_client_switched(self, new_master: Player) -> None:
        self.masters.append(new_master.actor_number)


def _service_both_until(
    clients: tuple[RealtimeClient, RealtimeClient], condition: Callable[[], bool]
) -> None:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out; states {[c.state for c in clients]}")
        for client in clients:
            client.service()
        time.sleep(1 / 30)


@dataclass
class _Vector:
    x: float
    y: float


@pytest.fixture
def vector_type() -> Iterator[None]:
    register_type(
        _Vector,
        code=ord("V"),
        serialize=lambda v: struct.pack(">ff", v.x, v.y),
        deserialize=lambda data: _Vector(*struct.unpack(">ff", data)),
    )
    yield
    unregister_type(_Vector)


@pytest.mark.usefixtures("vector_type")
@pytest.mark.parametrize(
    ("host_protocol", "guest_protocol", "host_serialization"),
    [
        pytest.param(ConnectionProtocol.Tcp, ConnectionProtocol.Tcp, 18, id="tcp"),
        pytest.param(ConnectionProtocol.Udp, ConnectionProtocol.Udp, 18, id="udp"),
        pytest.param(
            ConnectionProtocol.WebSocketSecure,
            ConnectionProtocol.WebSocketSecure,
            18,
            id="wss",
        ),
        pytest.param(
            ConnectionProtocol.Udp,
            ConnectionProtocol.WebSocketSecure,
            16,
            id="udp-gp16-with-wss-gp18",
        ),
    ],
)
def test_two_clients_exchange_events_and_properties(
    host_protocol: ConnectionProtocol,
    guest_protocol: ConnectionProtocol,
    host_serialization: int,
) -> None:
    room_name = f"pyphotonrealtime-e2e-{uuid.uuid4().hex[:8]}"
    host = _connected_client(
        host_protocol,
        SerializationProtocol.V16
        if host_serialization == 16
        else SerializationProtocol.V18,
    )
    guest = _connected_client(guest_protocol)
    host_seen, guest_seen = _InRoomRecorder(), _InRoomRecorder()
    host.add_callback_target(host_seen)
    guest.add_callback_target(guest_seen)
    both = (host, guest)
    assert host.op_create_room(
        EnterRoomParams(room_name=room_name, room_options=RoomOptions(max_players=2))
    )
    _service_until(host, ClientState.Joined)
    assert guest.op_join_room(EnterRoomParams(room_name=room_name))
    _service_both_until(both, lambda: guest.in_room and host_seen.entered == [2])
    host_room, guest_room = host.current_room, guest.current_room
    assert host_room is not None
    assert guest_room is not None

    # Custom events, both directions, with routing.
    assert guest.op_raise_event(7, {"hello": [1, 2.5, "x"]})
    assert host.op_raise_event(
        8, "to-guest", RaiseEventArgs(target_actors=[2]), SendOptions(encrypt=True)
    )
    _service_both_until(both, lambda: bool(host_seen.events and guest_seen.events))
    (event,) = host_seen.events
    assert (event.code, event.sender, event.custom_data) == (
        7,
        2,
        {"hello": [1, 2.5, "x"]},
    )
    (event,) = guest_seen.events
    assert (event.code, event.sender, event.custom_data) == (8, 1, "to-guest")

    # Large payloads (UDP fragments them), custom types, unreliable sends.
    blob = os.urandom(20_000)
    assert guest.op_raise_event(9, {"blob": blob, "at": _Vector(1.5, -2.0)})
    for _ in range(3):
        assert guest.op_raise_event(10, None, send_options=SendOptions(reliable=False))
    _service_both_until(both, lambda: any(e.code == 9 for e in host_seen.events))
    (big,) = [e for e in host_seen.events if e.code == 9]
    assert big.custom_data == {"blob": blob, "at": _Vector(1.5, -2.0)}
    _service_both_until(both, lambda: any(e.code == 10 for e in host_seen.events))

    # Room and player properties propagate to the other client.
    assert host_room.set_custom_properties({"round": 1})
    assert guest.local_player.set_custom_properties({"ready": True})
    _service_both_until(
        both,
        lambda: (
            guest_room.custom_properties.get("round") == 1
            and host_room.players[2].custom_properties.get("ready") is True
        ),
    )
    assert host_room.custom_properties == {"round": 1}
    assert guest.local_player.custom_properties == {"ready": True}

    # Compare and swap: only the matching expectation is applied.
    assert guest_room.set_custom_properties({"round": 9}, expected={"round": 0})
    assert guest_room.set_custom_properties({"round": 2}, expected={"round": 1})
    _service_both_until(both, lambda: host_room.custom_properties["round"] == 2)
    for _ in range(10):  # Give a wrongly accepted swap time to show up.
        for client in both:
            client.service()
        time.sleep(1 / 30)
    assert host_room.custom_properties == {"round": 2}
    assert guest_room.custom_properties == {"round": 2}

    # Hand the master client over, then leave: the host sees the guest go.
    assert host_room.set_master_client(host_room.players[2])
    _service_both_until(
        both, lambda: host_room.master_client_id == guest_room.master_client_id == 2
    )
    assert host_seen.masters == guest_seen.masters == [2]
    assert guest.op_leave_room()
    _service_both_until(both, lambda: host_seen.left == [2])
    assert host_room.master_client_id == 1
    assert 2 not in host_room.players
    guest.disconnect()
    host.disconnect()


class _StatusRecorder:
    def __init__(self) -> None:
        self.statuses: list[StatusCode] = []

    def on_status_changed(self, status: StatusCode) -> None:
        self.statuses.append(status)

    def on_operation_response(self, response: OperationResponse) -> None:
        pass

    def on_event(self, event: EventData) -> None:
        pass


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_peer_init_and_encryption_with_name_server(
    protocol: ConnectionProtocol,
) -> None:
    listener = _StatusRecorder()
    peer = PhotonPeer(listener, create_transport(protocol), keep_alive_interval=0.2)
    host = NAME_SERVER.rpartition(":")[0]
    assert peer.connect(f"{host}:{NAME_SERVER_PORTS[protocol]}", APP_ID)

    deadline = time.monotonic() + TIMEOUT_SECONDS
    while peer.last_round_trip_time is None or not peer.is_encryption_available:
        if time.monotonic() > deadline:
            pytest.fail(f"timed out; statuses so far: {listener.statuses}")
        peer.service()
        if peer.state == PeerState.Connected and not peer.is_encryption_available:
            peer.establish_encryption()  # No-op while an exchange is in flight.
        time.sleep(1 / 30)

    assert listener.statuses == [StatusCode.Connect, StatusCode.EncryptionEstablished]
    peer.disconnect()
