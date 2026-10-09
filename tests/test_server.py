"""``PhotonServer`` over real sockets, driven by ``RealtimeClient``."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, override

import pytest

from pyphotonrealtime import AppSettings, ClientState, RealtimeClient
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.property_keys import GamePropertyKey
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol
from pyphotonrealtime.realtime import (
    AuthMode,
    DisconnectCause,
    EnterRoomParams,
    ErrorCode,
    EventCaching,
    FriendInfo,
    InRoomCallbacks,
    LobbyCallbacks,
    MatchmakingCallbacks,
    OnEventCallback,
    RaiseEventArgs,
    ReceiverGroup,
    RoomOptions,
    TypedLobby,
)
from pyphotonrealtime.server import PhotonServer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pyphotonrealtime.peer import EventData
    from pyphotonrealtime.realtime import Player, RoomInfo

APP_ID = "6f869876-bfbc-491e-8fff-4210c966f145"
TIMEOUT = 5.0
ROOM = "room-1"


class Recorder(InRoomCallbacks, MatchmakingCallbacks, LobbyCallbacks, OnEventCallback):
    def __init__(self) -> None:
        self.events: list[tuple[int, int, Any]] = []
        self.calls: list[tuple[str, Any]] = []
        self.rooms: dict[str, RoomInfo] = {}

    @override
    def on_event(self, event: EventData) -> None:
        if event.code < 200:
            self.events.append((event.code, event.sender, event.custom_data))

    @override
    def on_player_entered_room(self, player: Player) -> None:
        self.calls.append(("entered", player.actor_number))

    @override
    def on_player_left_room(self, player: Player) -> None:
        self.calls.append(("left", player.actor_number))

    @override
    def on_master_client_switched(self, new_master: Player) -> None:
        self.calls.append(("master", new_master.actor_number))

    @override
    def on_create_room_failed(self, return_code: int, message: str) -> None:
        self.calls.append(("create failed", return_code))

    @override
    def on_join_room_failed(self, return_code: int, message: str) -> None:
        self.calls.append(("join failed", return_code))

    @override
    def on_join_random_failed(self, return_code: int, message: str) -> None:
        self.calls.append(("random failed", return_code))

    @override
    def on_room_list_update(self, rooms: list[RoomInfo]) -> None:
        for room in rooms:
            if room.removed_from_list:
                self.rooms.pop(room.name, None)
            else:
                self.rooms[room.name] = room

    @override
    def on_room_properties_update(self, changed: dict[Any, Any]) -> None:
        self.calls.append(("room props", changed))

    @override
    def on_player_properties_update(
        self, player: Player, changed: dict[Any, Any]
    ) -> None:
        self.calls.append(("player props", (player.actor_number, changed)))

    @override
    def on_friend_list_update(self, friends: list[FriendInfo]) -> None:
        self.calls.append(("friends", friends))


@pytest.fixture
def server() -> Iterator[PhotonServer]:
    with PhotonServer(
        name_server_port=0, master_server_port=0, game_server_port=0
    ) as server:
        yield server


type Connect = Callable[..., tuple[RealtimeClient, Recorder]]


@pytest.fixture
def connect(server: PhotonServer) -> Iterator[Connect]:
    """Connect a client through the Name Server to the Master Server."""
    clients: list[RealtimeClient] = []

    def connect(
        nick: str = "",
        serialization: SerializationProtocol = SerializationProtocol.V18,
        **settings: Any,  # noqa: ANN401
    ) -> tuple[RealtimeClient, Recorder]:
        client = RealtimeClient()
        client.peer.serialization_protocol = serialization
        client.local_player.nick_name = nick
        recorder = Recorder()
        client.add_callback_target(recorder)
        settings = {
            "name_server": server.host,
            "name_server_port": server.name_server_port,
            "fixed_region": "local",
            **settings,
        }
        assert client.connect_using_settings(
            AppSettings(app_id_realtime=APP_ID, **settings)
        )
        clients.append(client)
        wait(lambda: client.state == ClientState.ConnectedToMasterServer, client)
        return client, recorder

    yield connect
    for client in clients:
        client.disconnect()


def wait(done: Callable[[], bool], *clients: RealtimeClient) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not done():
        if time.monotonic() > deadline:
            states = [(c.state, c.disconnect_cause) for c in clients]
            pytest.fail(f"timed out: {states}")
        for client in clients:
            client.service()
        time.sleep(0.005)


def settle(*clients: RealtimeClient, seconds: float = 0.2) -> None:
    """Service for a while, to show that something does *not* arrive."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for client in clients:
            client.service()
        time.sleep(0.005)


def create(
    client: RealtimeClient,
    name: str = ROOM,
    **options: Any,  # noqa: ANN401
) -> None:
    assert client.op_create_room(
        EnterRoomParams(room_name=name, room_options=RoomOptions(**options))
    )
    wait(lambda: client.state == ClientState.Joined, client)


def join(client: RealtimeClient, *others: RealtimeClient, name: str = ROOM) -> None:
    assert client.op_join_room(EnterRoomParams(room_name=name))
    wait(lambda: client.state == ClientState.Joined, client, *others)


def joined(client: RealtimeClient) -> Callable[[], bool]:
    return lambda: client.state == ClientState.Joined


def in_room(*clients: RealtimeClient) -> Callable[[], bool]:
    def done() -> bool:
        return all(
            c.current_room is not None and len(c.current_room.players) == len(clients)
            for c in clients
        )

    return done


# -- connecting ----------------------------------------------------------------


@pytest.mark.parametrize("auth_mode", list(AuthMode))
@pytest.mark.parametrize(
    "protocol", [SerializationProtocol.V16, SerializationProtocol.V18]
)
def test_connects_through_the_name_server(
    connect: Connect, auth_mode: AuthMode, protocol: SerializationProtocol
) -> None:
    client, _ = connect(serialization=protocol, auth_mode=auth_mode, fixed_region=None)
    # No fixed region: the client listed and pinged ours.
    assert client.cloud_region == "local"
    assert client.current_cluster == "default"
    assert client.user_id


def test_user_id_is_kept(connect: Connect) -> None:
    client, _ = connect()
    assert client.op_create_room(EnterRoomParams(room_name=ROOM))
    other, _ = connect()
    assert client.user_id != other.user_id


def test_connects_straight_to_the_master_server(
    server: PhotonServer, connect: Connect
) -> None:
    client, _ = connect(
        use_name_server=False, server=server.host, port=server.master_server_port
    )
    create(client)
    assert client.current_room is not None


def test_rejects_other_app_ids() -> None:
    with PhotonServer(
        name_server_port=0,
        master_server_port=0,
        game_server_port=0,
        app_id="11111111-1111-1111-1111-111111111111",
    ) as server:
        client = RealtimeClient()
        client.connect_using_settings(
            AppSettings(
                app_id_realtime=APP_ID,
                name_server=server.host,
                name_server_port=server.name_server_port,
                fixed_region="local",
            )
        )
        wait(lambda: client.state == ClientState.Disconnected, client)
        assert client.disconnect_cause == DisconnectCause.InvalidAuthentication


def test_unknown_region(server: PhotonServer) -> None:
    client = RealtimeClient()
    client.connect_using_settings(
        AppSettings(
            app_id_realtime=APP_ID,
            name_server=server.host,
            name_server_port=server.name_server_port,
            fixed_region="mars",
        )
    )
    wait(lambda: client.state == ClientState.Disconnected, client)
    assert client.disconnect_cause == DisconnectCause.InvalidRegion


# -- matchmaking -------------------------------------------------------------------


def test_create_and_join(connect: Connect) -> None:
    alice, alice_events = connect("alice")
    bob, _ = connect("bob")
    create(alice, max_players=4, custom_room_properties={"map": "dust"})
    join(bob, alice)
    wait(in_room(alice, bob), alice, bob)

    assert alice.current_room is not None
    assert bob.current_room is not None
    assert alice.local_player.actor_number == 1
    assert bob.local_player.actor_number == 2
    assert bob.current_room.players[1].nick_name == "alice"
    assert alice.current_room.players[2].nick_name == "bob"
    assert bob.current_room.max_players == 4
    assert bob.current_room.custom_properties == {"map": "dust"}
    assert bob.current_room.master_client_id == 1
    assert ("entered", 2) in alice_events.calls


def test_create_without_a_name(connect: Connect) -> None:
    client, _ = connect()
    assert client.op_create_room(EnterRoomParams())
    wait(lambda: client.state == ClientState.Joined, client)
    assert client.current_room is not None
    assert client.current_room.name


def test_create_existing_room_fails(connect: Connect) -> None:
    alice, _ = connect()
    bob, bob_events = connect()
    create(alice)
    assert bob.op_create_room(EnterRoomParams(room_name=ROOM))
    wait(lambda: bool(bob_events.calls), bob)
    assert bob_events.calls == [("create failed", ErrorCode.GameIdAlreadyExists)]
    assert bob.state == ClientState.ConnectedToMasterServer


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"max_players": 1}, ErrorCode.GameFull),
        ({"is_open": False}, ErrorCode.GameClosed),
        (None, ErrorCode.GameDoesNotExist),
    ],
)
def test_join_fails(
    connect: Connect, options: dict[str, Any] | None, expected: ErrorCode
) -> None:
    alice, _ = connect()
    bob, bob_events = connect()
    if options is not None:
        create(alice, **options)
    assert bob.op_join_room(EnterRoomParams(room_name=ROOM))
    wait(lambda: bool(bob_events.calls), bob)
    assert bob_events.calls == [("join failed", expected)]


def test_join_or_create(connect: Connect) -> None:
    alice, _ = connect()
    bob, _ = connect()
    for client in (alice, bob):
        assert client.op_join_or_create_room(EnterRoomParams(room_name=ROOM))
        wait(joined(client), client)
    wait(in_room(alice, bob), alice, bob)


def test_join_random(connect: Connect) -> None:
    alice, _ = connect()
    bob, bob_events = connect()
    assert bob.op_join_random_room()
    wait(lambda: bool(bob_events.calls), bob)
    assert bob_events.calls == [("random failed", ErrorCode.NoRandomMatchFound)]

    create(alice, custom_room_properties={"mode": 2})
    bob_events.calls.clear()
    assert bob.op_join_random_room({"mode": 3})
    wait(lambda: bool(bob_events.calls), bob)
    assert bob_events.calls == [("random failed", ErrorCode.NoRandomMatchFound)]

    assert bob.op_join_random_room({"mode": 2})
    wait(lambda: bob.state == ClientState.Joined, bob, alice)
    assert bob.current_room is not None
    assert bob.current_room.name == ROOM


def test_lobby_lists_rooms(connect: Connect) -> None:
    alice, _ = connect()
    bob, bob_events = connect()
    assert bob.op_join_lobby()
    wait(lambda: bob.in_lobby, bob)

    create(alice, max_players=3)
    wait(lambda: ROOM in bob_events.rooms, bob, alice)
    listing = bob_events.rooms[ROOM]
    assert listing.player_count == 1
    assert listing.max_players == 3

    # Leaving empties the room; it's removed from the list.
    assert alice.op_leave_room()
    wait(lambda: ROOM not in bob_events.rooms, bob, alice)


def test_lobby_hides_invisible_rooms(connect: Connect) -> None:
    alice, _ = connect()
    bob, bob_events = connect()
    create(alice, "hidden", is_visible=False)
    create_other, _ = connect()
    create(create_other, "shown")
    assert bob.op_join_lobby(TypedLobby())
    wait(lambda: "shown" in bob_events.rooms, bob)
    assert "hidden" not in bob_events.rooms


def test_find_friends(connect: Connect) -> None:
    alice, _ = connect()
    bob, bob_events = connect()
    create(alice)
    assert alice.user_id is not None
    assert bob.op_find_friends([alice.user_id, "nobody"])
    wait(lambda: bool(bob_events.calls), bob, alice)
    ((_, friends),) = bob_events.calls
    assert friends == [
        FriendInfo(alice.user_id, is_online=True, room=ROOM),
        FriendInfo("nobody", is_online=False),
    ]


# -- in the room ---------------------------------------------------------------------


@pytest.fixture
def trio(connect: Connect) -> tuple[list[RealtimeClient], list[Recorder]]:
    pairs = [connect(name) for name in ("alice", "bob", "carol")]
    clients = [c for c, _ in pairs]
    create(clients[0])
    for client in clients[1:]:
        join(client, *clients)
    wait(in_room(*clients), *clients)
    return clients, [r for _, r in pairs]


@pytest.mark.parametrize(
    ("args", "receivers"),
    [
        (None, {1, 2}),
        (RaiseEventArgs(receivers=ReceiverGroup.All), {0, 1, 2}),
        (RaiseEventArgs(receivers=ReceiverGroup.MasterClient), {0}),
        (RaiseEventArgs(target_actors=[3]), {2}),
    ],
)
def test_raise_event_receivers(
    trio: tuple[list[RealtimeClient], list[Recorder]],
    args: RaiseEventArgs | None,
    receivers: set[int],
) -> None:
    clients, recorders = trio
    assert clients[0].op_raise_event(7, {"hp": 3}, args)
    wait(lambda: all(recorders[i].events for i in receivers), *clients)
    settle(*clients)
    for i, recorder in enumerate(recorders):
        assert recorder.events == ([(7, 1, {"hp": 3})] if i in receivers else [])


def test_interest_groups(trio: tuple[list[RealtimeClient], list[Recorder]]) -> None:
    clients, recorders = trio
    assert clients[1].op_change_groups(None, [5])
    settle(*clients)
    assert clients[0].op_raise_event(1, "x", RaiseEventArgs(interest_group=5))
    wait(lambda: bool(recorders[1].events), *clients)
    settle(*clients)
    assert recorders[2].events == []


def test_cached_events_reach_late_joiners(connect: Connect) -> None:
    alice, _ = connect()
    create(alice)
    cache = RaiseEventArgs(caching=EventCaching.AddToRoomCache)
    assert alice.op_raise_event(1, "first", cache)
    assert alice.op_raise_event(2, "second", cache)
    assert alice.op_raise_event(
        1, None, RaiseEventArgs(caching=EventCaching.RemoveFromRoomCache)
    )
    settle(alice)

    bob, bob_events = connect()
    join(bob, alice)
    wait(lambda: bool(bob_events.events), bob, alice)
    settle(bob, alice)
    assert bob_events.events == [(2, 1, "second")]


def test_cache_is_cleaned_when_the_sender_leaves(connect: Connect) -> None:
    alice, _ = connect()
    bob, _ = connect()
    create(alice)
    join(bob, alice)
    assert bob.op_raise_event(
        1, "bob's", RaiseEventArgs(caching=EventCaching.AddToRoomCache)
    )
    settle(alice, bob)
    assert bob.op_leave_room()
    wait(lambda: bob.state == ClientState.ConnectedToMasterServer, alice, bob)

    carol, carol_events = connect()
    join(carol, alice)
    settle(carol, alice)
    assert carol_events.events == []


def test_room_properties(trio: tuple[list[RealtimeClient], list[Recorder]]) -> None:
    clients, recorders = trio
    assert clients[0].op_set_properties_of_room({"score": 1})
    wait(
        lambda: all(
            c.current_room is not None and c.current_room.custom_properties
            for c in clients
        ),
        *clients,
    )
    # Without expected values the sender updated itself and isn't told again.
    settle(*clients)
    assert ("room props", {"score": 1}) not in recorders[0].calls
    assert ("room props", {"score": 1}) in recorders[1].calls


def test_room_property_compare_and_swap(
    trio: tuple[list[RealtimeClient], list[Recorder]],
) -> None:
    clients, recorders = trio
    assert clients[0].op_set_properties_of_room({"turn": 1})
    settle(*clients)
    assert clients[1].op_set_properties_of_room({"turn": 2}, {"turn": 0})
    settle(*clients)
    assert all(c.current_room.custom_properties["turn"] == 1 for c in clients)  # type: ignore[union-attr]

    assert clients[1].op_set_properties_of_room({"turn": 2}, {"turn": 1})
    # With expected values everyone is told, the sender included.
    wait(lambda: ("room props", {"turn": 2}) in recorders[1].calls, *clients)
    wait(lambda: ("room props", {"turn": 2}) in recorders[0].calls, *clients)


def test_player_properties(trio: tuple[list[RealtimeClient], list[Recorder]]) -> None:
    clients, recorders = trio
    assert clients[1].local_player.set_custom_properties({"ready": True})
    wait(
        lambda: ("player props", (2, {"ready": True})) in recorders[2].calls,
        *clients,
    )
    carol, carol_events = clients[2], recorders[2]
    assert carol.current_room is not None
    assert carol.current_room.players[2].custom_properties == {"ready": True}
    assert carol_events.calls.count(("player props", (2, {"ready": True}))) == 1


def test_closing_the_room(trio: tuple[list[RealtimeClient], list[Recorder]]) -> None:
    clients, _ = trio
    is_open = Int8Parameter(GamePropertyKey.IsOpen.value.value)
    assert clients[0].op_set_properties_of_room(
        {is_open: BooleanParameter(value=False)}
    )
    wait(
        lambda: all(
            c.current_room is not None and not c.current_room.is_open for c in clients
        ),
        *clients,
    )


def test_master_client_hands_over(
    trio: tuple[list[RealtimeClient], list[Recorder]],
) -> None:
    clients, recorders = trio
    room = clients[0].current_room
    assert room is not None
    assert room.set_master_client(room.players[3])
    wait(lambda: ("master", 3) in recorders[1].calls, *clients)

    assert clients[2].op_leave_room()
    wait(lambda: ("left", 3) in recorders[0].calls, *clients)
    # The leaving master's job goes to the lowest remaining actor.
    wait(lambda: ("master", 1) in recorders[1].calls, *clients)
    assert clients[1].current_room is not None
    assert clients[1].current_room.master_client_id == 1


def test_disconnect_leaves_the_room(
    trio: tuple[list[RealtimeClient], list[Recorder]],
) -> None:
    clients, recorders = trio
    clients[1].disconnect()
    wait(lambda: ("left", 2) in recorders[0].calls, clients[0], clients[2])
    assert clients[0].current_room is not None
    assert set(clients[0].current_room.players) == {1, 3}


def test_rejoin_within_player_ttl(connect: Connect) -> None:
    alice, alice_events = connect()
    bob, _ = connect()
    create(alice, player_ttl=60_000)
    join(bob, alice)
    assert bob.op_leave_room(become_inactive=True)
    wait(lambda: ("left", 2) in alice_events.calls, alice, bob)
    assert alice.current_room is not None
    assert alice.current_room.players[2].is_inactive

    wait(lambda: bob.state == ClientState.ConnectedToMasterServer, bob, alice)
    assert bob.op_rejoin_room(ROOM)
    wait(lambda: bob.state == ClientState.Joined, bob, alice)
    assert bob.local_player.actor_number == 2
    wait(lambda: not alice.current_room.players[2].is_inactive, alice, bob)  # type: ignore[union-attr]


def test_empty_rooms_are_removed(connect: Connect, server: PhotonServer) -> None:
    alice, _ = connect()
    create(alice)
    assert alice.op_leave_room()
    wait(lambda: alice.state == ClientState.ConnectedToMasterServer, alice)
    wait(lambda: ROOM not in server.rooms, alice)


def test_inactive_players_expire(connect: Connect) -> None:
    alice, _ = connect()
    bob, _ = connect()
    create(alice, player_ttl=100)
    join(bob, alice)
    bob.disconnect()
    wait(
        lambda: alice.current_room is not None and 2 not in alice.current_room.players,
        alice,
    )
