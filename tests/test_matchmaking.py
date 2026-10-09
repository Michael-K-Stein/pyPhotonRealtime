"""Lobbies, matchmaking and the Game Server hop against fake servers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import pytest

from fake_cloud import (
    APP_ID,
    GAME_SERVER,
    MASTER_SERVER,
    NAME_SERVER,
    FakeCloud,
    FakeServer,
)
from pyphotonrealtime.protocol.event_code import EventCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.hashtable_param import HashtableParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.realtime import (
    AppSettings,
    ClientState,
    ConnectionCallbacks,
    DisconnectCause,
    EnterRoomParams,
    ErrorCode,
    FriendInfo,
    JoinMode,
    LobbyCallbacks,
    LobbyStatistics,
    LobbyType,
    MatchmakingCallbacks,
    MatchmakingMode,
    RealtimeClient,
    RoomOptions,
    ServerType,
    TypedLobby,
)
from pyphotonrealtime.realtime._convert import to_hashtable, to_param, to_python

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.enum_lookups import CommandParams
    from pyphotonrealtime.realtime import RoomInfo

ROOM = "room-1"


def b(key: int) -> Int8Parameter:
    return Int8Parameter(key)


def strings(*values: str) -> SliceParameter[StringParameter]:
    return SliceParameter([StringParameter(v) for v in values])


def room_list(rooms: dict[str, dict[Any, Any]]) -> CommandParams:
    return {
        ParameterKey.GameList: HashtableParameter(
            {
                StringParameter(name): to_hashtable(props)
                for name, props in rooms.items()
            }
        )
    }


class Recorder(ConnectionCallbacks, MatchmakingCallbacks, LobbyCallbacks):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    @override
    def on_connected_to_master(self) -> None:
        self.calls.append(("master", None))

    @override
    def on_disconnected(self, cause: DisconnectCause) -> None:
        self.calls.append(("disconnected", cause))

    @override
    def on_joined_lobby(self) -> None:
        self.calls.append(("joined_lobby", None))

    @override
    def on_left_lobby(self) -> None:
        self.calls.append(("left_lobby", None))

    @override
    def on_room_list_update(self, rooms: list[RoomInfo]) -> None:
        self.calls.append(("rooms", sorted(room.name for room in rooms)))

    @override
    def on_lobby_statistics_update(self, stats: list[LobbyStatistics]) -> None:
        self.calls.append(("lobby_stats", stats))

    @override
    def on_friend_list_update(self, friends: list[FriendInfo]) -> None:
        self.calls.append(("friends", friends))

    @override
    def on_created_room(self) -> None:
        self.calls.append(("created", None))

    @override
    def on_create_room_failed(self, return_code: int, message: str) -> None:
        self.calls.append(("create_failed", return_code))

    @override
    def on_joined_room(self) -> None:
        self.calls.append(("joined", None))

    @override
    def on_join_room_failed(self, return_code: int, message: str) -> None:
        self.calls.append(("join_failed", return_code))

    @override
    def on_join_random_failed(self, return_code: int, message: str) -> None:
        self.calls.append(("random_failed", return_code))

    @override
    def on_left_room(self) -> None:
        self.calls.append(("left", None))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


GAME_TOKEN = "game-token"  # noqa: S105 -- fake token
WRONG_GAME_SERVER = 32738


def enter_on_master(request: CommandParams) -> tuple[int, CommandParams]:
    room = request.get(ParameterKey.GameId, StringParameter(ROOM))
    return 0, {
        ParameterKey.Address: StringParameter(GAME_SERVER),
        ParameterKey.GameId: room,
        # Like Photon Cloud: a token only the chosen Game Server accepts.
        ParameterKey.Token: StringParameter(GAME_TOKEN),
    }


def authenticate_on_game_server(request: CommandParams) -> tuple[int, CommandParams]:
    token = request.get(ParameterKey.Token)
    if token is None or token.value != GAME_TOKEN:
        return WRONG_GAME_SERVER, {}
    return 0, {}


def enter_on_game_server(_request: CommandParams) -> tuple[int, CommandParams]:
    return 0, {
        ParameterKey.ActorNr: Int32Parameter(2),
        ParameterKey.GameProperties: to_hashtable(
            {b(255): b(4), b(253): True, b(248): Int32Parameter(1), "mode": "ffa"}
        ),
        ParameterKey.PlayerProperties: HashtableParameter(
            {
                Int32Parameter(1): to_hashtable({b(255): "host", "team": "red"}),
                Int32Parameter(2): to_hashtable({b(255): "me"}),
            }
        ),
        ParameterKey.ActorList: SliceParameter([Int32Parameter(1), Int32Parameter(2)]),
    }


@pytest.fixture
def name_server() -> FakeServer:
    return FakeServer(NAME_SERVER)


@pytest.fixture
def master_server() -> FakeServer:
    server = FakeServer(MASTER_SERVER)
    for code in (
        OperationCode.CreateGame,
        OperationCode.JoinGame,
        OperationCode.JoinRandomGame,
    ):
        server.handlers[code] = enter_on_master
    return server


@pytest.fixture
def game_server() -> FakeServer:
    server = FakeServer(GAME_SERVER)
    server.handlers[OperationCode.Authenticate] = authenticate_on_game_server
    server.handlers[OperationCode.CreateGame] = enter_on_game_server
    server.handlers[OperationCode.JoinGame] = enter_on_game_server
    return server


@pytest.fixture
def cloud(
    name_server: FakeServer, master_server: FakeServer, game_server: FakeServer
) -> FakeCloud:
    return FakeCloud(name_server, master_server, game_server)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


def run(client: RealtimeClient, until: ClientState, steps: int = 50) -> None:
    for _ in range(steps):
        if client.state == until:
            return
        client.service()
    pytest.fail(f"stuck in {client.state}, waiting for {until}")


@pytest.fixture
def client(cloud: FakeCloud, recorder: Recorder) -> RealtimeClient:
    client = RealtimeClient(transport=cloud)
    client.local_player.nick_name = "me"
    client.add_callback_target(recorder)
    client.connect_using_settings(
        AppSettings(app_id_realtime=APP_ID, name_server="ns.example", fixed_region="eu")
    )
    run(client, ClientState.ConnectedToMasterServer)
    recorder.calls.clear()
    return client


# -- conversion -----------------------------------------------------------


def test_to_param_round_trips_plain_values() -> None:
    value = {"a": [1, 2.5, "x", None, True], 2: b"\x01", "big": 2**40, "t": (1,)}
    param = to_param(value)
    assert isinstance(param, HashtableParameter)
    assert to_python(param) == {**value, "t": [1]}
    assert to_param(b(3)) == b(3)
    with pytest.raises(TypeError, match="set"):
        to_param({1, 2})


# -- lobby --------------------------------------------------------------


def test_join_lobby_and_room_list_updates(
    client: RealtimeClient,
    cloud: FakeCloud,
    master_server: FakeServer,
    recorder: Recorder,
) -> None:
    lobby = TypedLobby("ranked", LobbyType.SqlLobby)
    assert client.op_join_lobby(lobby)
    assert client.state == ClientState.JoiningLobby
    run(client, ClientState.JoinedLobby)
    assert client.in_lobby
    assert client.current_lobby == lobby
    assert master_server.ops(OperationCode.JoinLobby) == [
        {
            ParameterKey.LobbyName: StringParameter("ranked"),
            ParameterKey.LobbyType: b(2),
        }
    ]

    cloud.push_event(
        EventCode.GameList,
        room_list(
            {
                "a": {b(255): b(4), b(252): b(1), "C0": 3},
                "b": {b(255): b(2), b(253): False},
            }
        ),
    )
    client.service()
    assert set(client.room_list) == {"a", "b"}
    a = client.room_list["a"]
    assert (a.max_players, a.player_count, a.is_open) == (4, 1, True)
    assert a.custom_properties == {"C0": 3}
    assert not client.room_list["b"].is_open

    cloud.push_event(
        EventCode.GameListUpdate,
        room_list({"a": {b(252): b(2)}, "b": {b(251): True}, "c": {b(255): b(8)}}),
    )
    client.service()
    assert set(client.room_list) == {"a", "c"}
    assert client.room_list["a"].player_count == 2
    assert client.room_list["a"].custom_properties == {"C0": 3}
    assert recorder.calls == [
        ("joined_lobby", None),
        ("rooms", ["a", "b"]),
        ("rooms", ["a", "b", "c"]),
    ]

    assert client.op_get_game_list(lobby, "C0 > 1")
    client.service()
    ((params),) = master_server.ops(OperationCode.GetGameList)
    assert params[ParameterKey.SqlLobbyFilter] == StringParameter("C0 > 1")
    assert not client.op_get_game_list(TypedLobby("plain"), "C0 > 1")

    assert client.op_leave_lobby()
    run(client, ClientState.ConnectedToMasterServer)
    assert not client.in_lobby
    assert client.room_list == {}
    assert recorder.calls[-1] == ("left_lobby", None)


def test_default_lobby_sends_no_parameters(
    client: RealtimeClient, master_server: FakeServer
) -> None:
    client.op_join_lobby()
    run(client, ClientState.JoinedLobby)
    assert master_server.ops(OperationCode.JoinLobby) == [{}]


def test_lobby_and_app_statistics(
    client: RealtimeClient, cloud: FakeCloud, recorder: Recorder
) -> None:
    cloud.push_event(
        EventCode.LobbyStats,
        {
            ParameterKey.LobbyName: strings("", "ranked"),
            ParameterKey.LobbyType: Int8SliceParameter(b"\x00\x02"),
            ParameterKey.PeerCount: SliceParameter(
                [Int32Parameter(5), Int32Parameter(1)]
            ),
            ParameterKey.GameCount: SliceParameter(
                [Int32Parameter(2), Int32Parameter(0)]
            ),
        },
    )
    cloud.push_event(
        EventCode.AppStats,
        {
            ParameterKey.MasterPeerCount: Int32Parameter(3),
            ParameterKey.PeerCount: Int32Parameter(7),
            ParameterKey.GameCount: Int32Parameter(2),
        },
    )
    client.service()
    assert client.lobby_statistics == [
        LobbyStatistics(TypedLobby(), 5, 2),
        LobbyStatistics(TypedLobby("ranked", LobbyType.SqlLobby), 1, 0),
    ]
    assert recorder.calls == [("lobby_stats", client.lobby_statistics)]
    counts = (
        client.players_on_master_count,
        client.players_in_rooms_count,
        client.rooms_count,
    )
    assert counts == (3, 7, 2)


# -- entering rooms -------------------------------------------------------


def test_create_room_hops_to_game_server(
    client: RealtimeClient,
    cloud: FakeCloud,
    master_server: FakeServer,
    game_server: FakeServer,
    recorder: Recorder,
) -> None:
    options = RoomOptions(
        max_players=4,
        player_ttl=60_000,
        custom_room_properties={"mode": "ffa"},
        custom_room_properties_for_lobby=["mode"],
        publish_user_id=True,
        plugins=["Chess"],
    )
    params = EnterRoomParams(
        room_name=ROOM,
        room_options=options,
        player_properties={"team": "blue"},
        expected_users=["bob"],
    )
    assert client.op_create_room(params)
    assert client.state == ClientState.Joining
    run(client, ClientState.Joined)

    assert cloud.connections[-2:] == [MASTER_SERVER, GAME_SERVER]
    assert client.server == ServerType.GameServer
    assert client.game_server_address == GAME_SERVER
    assert client.in_room
    assert recorder.names() == ["created", "joined"]

    # The Master Server only gets the name, lobby and expected users.
    assert master_server.ops(OperationCode.CreateGame) == [
        {ParameterKey.GameId: StringParameter(ROOM), ParameterKey.Add: strings("bob")}
    ]
    # The Game Server authenticates with the Master's token, then gets the request.
    assert game_server.ops(OperationCode.Authenticate) == [
        {ParameterKey.Token: StringParameter(GAME_TOKEN)}
    ]
    (create,) = game_server.ops(OperationCode.CreateGame)
    assert create[ParameterKey.GameId] == StringParameter(ROOM)
    assert create[ParameterKey.PlayerTTL] == Int32Parameter(60_000)
    assert create[ParameterKey.PublishUserId] == BooleanParameter(value=True)
    assert create[ParameterKey.Plugins] == strings("Chess")
    assert create[ParameterKey.Broadcast] == BooleanParameter(value=True)
    assert to_python(create[ParameterKey.PlayerProperties]) == {
        "team": "blue",
        255: "me",
    }
    room_props = to_python(create[ParameterKey.GameProperties])
    assert room_props["mode"] == "ffa"
    assert (room_props[255], room_props[253], room_props[254]) == (4, True, True)
    assert room_props[250] == ["mode"]

    room = client.current_room
    assert room is not None
    assert (room.name, room.max_players, room.master_client_id) == (ROOM, 4, 1)
    assert room.custom_properties == {"mode": "ffa"}
    assert client.local_player.actor_number == 2
    assert room.players[2] is client.local_player
    host = room.master_client
    assert host is not None
    assert (host.nick_name, host.custom_properties) == ("host", {"team": "red"})


def test_join_random_room_with_filters(
    client: RealtimeClient, master_server: FakeServer, game_server: FakeServer
) -> None:
    assert client.op_join_random_room(
        {"mode": "ffa"},
        4,
        matchmaking_mode=MatchmakingMode.RandomMatching,
        lobby=TypedLobby("ranked", LobbyType.SqlLobby),
        sql_lobby_filter="C0 = 1",
    )
    run(client, ClientState.Joined)
    (request,) = master_server.ops(OperationCode.JoinRandomGame)
    assert to_python(request[ParameterKey.GameProperties]) == {"mode": "ffa", 255: 4}
    assert request[ParameterKey.MatchMakingType] == b(2)
    assert request[ParameterKey.LobbyName] == StringParameter("ranked")
    assert request[ParameterKey.SqlLobbyFilter] == StringParameter("C0 = 1")
    # The Game Server is asked to join the room the Master Server picked.
    (join,) = game_server.ops(OperationCode.JoinGame)
    assert join[ParameterKey.GameId] == StringParameter(ROOM)
    assert ParameterKey.GameProperties not in join


def test_join_or_create_sends_room_options_and_detects_creation(
    client: RealtimeClient, game_server: FakeServer, recorder: Recorder
) -> None:
    def created(_request: CommandParams) -> tuple[int, CommandParams]:
        return 0, {ParameterKey.ActorNr: Int32Parameter(1)}

    game_server.handlers[OperationCode.JoinGame] = created
    assert client.op_join_or_create_room(
        EnterRoomParams(room_name=ROOM, room_options=RoomOptions(max_players=300))
    )
    run(client, ClientState.Joined)
    (join,) = game_server.ops(OperationCode.JoinGame)
    assert join[ParameterKey.JoinMode] == b(JoinMode.CreateIfNotExists)
    room_props = to_python(join[ParameterKey.GameProperties])
    assert (room_props[255], room_props[243]) == (255, 300)
    assert recorder.names() == ["created", "joined"]


def test_join_room_needs_a_name(client: RealtimeClient) -> None:
    assert not client.op_join_room(EnterRoomParams())
    assert not client.op_join_or_create_room(EnterRoomParams())


@pytest.mark.parametrize(
    ("operation", "callback"),
    [
        (OperationCode.CreateGame, "create_failed"),
        (OperationCode.JoinGame, "join_failed"),
        (OperationCode.JoinRandomGame, "random_failed"),
    ],
)
def test_master_server_rejects(
    client: RealtimeClient,
    master_server: FakeServer,
    recorder: Recorder,
    operation: OperationCode,
    callback: str,
) -> None:
    def reject(_request: CommandParams) -> tuple[int, CommandParams]:
        return ErrorCode.NoRandomMatchFound, {}

    master_server.handlers[operation] = reject
    match operation:
        case OperationCode.CreateGame:
            client.op_create_room(EnterRoomParams(room_name=ROOM))
        case OperationCode.JoinGame:
            client.op_join_room(EnterRoomParams(room_name=ROOM))
        case _:
            client.op_join_random_room()
    run(client, ClientState.ConnectedToMasterServer)
    assert recorder.calls == [(callback, ErrorCode.NoRandomMatchFound)]
    assert client.server == ServerType.MasterServer


def test_failure_in_lobby_returns_to_lobby(
    client: RealtimeClient, master_server: FakeServer
) -> None:
    master_server.handlers[OperationCode.JoinRandomGame] = lambda _: (
        ErrorCode.NoRandomMatchFound,
        {},
    )
    client.op_join_lobby()
    run(client, ClientState.JoinedLobby)
    assert client.op_join_random_room()
    assert client.state == ClientState.Joining
    run(client, ClientState.JoinedLobby)


def test_game_server_rejects_and_client_returns_to_master(
    client: RealtimeClient,
    cloud: FakeCloud,
    game_server: FakeServer,
    recorder: Recorder,
) -> None:
    game_server.handlers[OperationCode.JoinGame] = lambda _: (ErrorCode.GameFull, {})
    client.op_join_room(EnterRoomParams(room_name=ROOM))
    run(client, ClientState.Joining)
    for _ in range(50):
        if ("master", None) in recorder.calls:
            break
        client.service()
    assert client.state == ClientState.ConnectedToMasterServer
    assert recorder.calls == [("join_failed", ErrorCode.GameFull), ("master", None)]
    assert cloud.connections[-1] == MASTER_SERVER
    assert client.current_room is None


def test_matchmaking_needs_master_server() -> None:
    client = RealtimeClient(transport=FakeCloud())
    assert not client.op_join_lobby()
    assert not client.op_leave_lobby()
    assert not client.op_create_room(EnterRoomParams())
    assert not client.op_join_random_room()
    assert not client.op_find_friends(["bob"])
    assert not client.op_leave_room()
    assert not client.reconnect_and_rejoin()


# -- leaving and rejoining ----------------------------------------------------


def test_leave_room_returns_to_master(
    client: RealtimeClient,
    cloud: FakeCloud,
    game_server: FakeServer,
    recorder: Recorder,
) -> None:
    client.op_join_room(EnterRoomParams(room_name=ROOM))
    run(client, ClientState.Joined)
    recorder.calls.clear()

    assert client.op_leave_room(become_inactive=True)
    assert client.state == ClientState.Leaving
    run(client, ClientState.ConnectedToMasterServer)
    assert game_server.ops(OperationCode.Leave) == [
        {ParameterKey.IsInactive: BooleanParameter(value=True)}
    ]
    assert recorder.calls == [("left", None), ("master", None)]
    assert client.current_room is None
    assert cloud.connections[-1] == MASTER_SERVER

    # Rejoin the room this client became inactive in.
    assert client.op_rejoin_room(ROOM)
    run(client, ClientState.Joined)
    rejoin = game_server.ops(OperationCode.JoinGame)[-1]
    assert rejoin[ParameterKey.JoinMode] == b(JoinMode.RejoinOnly)
    assert ParameterKey.PlayerProperties not in rejoin


def test_reconnect_and_rejoin_after_drop(
    client: RealtimeClient,
    cloud: FakeCloud,
    game_server: FakeServer,
    recorder: Recorder,
) -> None:
    client.op_create_room(EnterRoomParams(room_name=ROOM))
    run(client, ClientState.Joined)
    client.peer.transport.close()  # Connection drops.
    run(client, ClientState.Disconnected)
    assert client.current_room is None
    recorder.calls.clear()

    assert client.reconnect_and_rejoin()
    assert client.state == ClientState.ConnectingToGameServer
    run(client, ClientState.Joined)
    assert cloud.connections[-1] == GAME_SERVER
    assert game_server.ops(OperationCode.Authenticate)[-1] == {
        ParameterKey.Token: StringParameter(GAME_TOKEN)
    }
    rejoin = game_server.ops(OperationCode.JoinGame)[-1]
    assert rejoin[ParameterKey.GameId] == StringParameter(ROOM)
    assert rejoin[ParameterKey.JoinMode] == b(JoinMode.RejoinOnly)
    assert recorder.calls == [("joined", None)]
    assert client.disconnect_cause == DisconnectCause.NoCause

    # A deliberate disconnect forgets the room.
    client.disconnect()
    run(client, ClientState.Disconnected)
    assert not client.reconnect_and_rejoin()


# -- friends ----------------------------------------------------------------


def test_find_friends(
    client: RealtimeClient, master_server: FakeServer, recorder: Recorder
) -> None:
    def answer(_request: CommandParams) -> tuple[int, CommandParams]:
        return 0, {
            ParameterKey.FindFriendsRequestList: SliceParameter(
                [BooleanParameter(value=True), BooleanParameter(value=False)]
            ),
            ParameterKey.FindFriendsResponseRoomIdList: strings(ROOM, ""),
        }

    master_server.handlers[OperationCode.FindFriends] = answer
    assert not client.op_find_friends([])
    assert client.op_find_friends(["bob", "eve"])
    client.service()
    client.service()
    assert master_server.ops(OperationCode.FindFriends) == [
        {ParameterKey.FindFriendsRequestList: strings("bob", "eve")}
    ]
    ((name, friends),) = recorder.calls
    assert name == "friends"
    assert friends == [
        FriendInfo("bob", is_online=True, room=ROOM),
        FriendInfo("eve", is_online=False),
    ]
    assert friends[0].is_in_room
