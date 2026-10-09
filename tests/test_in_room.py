"""In-room events, properties and custom events against fake servers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import pytest

from fake_cloud import APP_ID, FakeCloud, FakeServer
from pyphotonrealtime.protocol.event_code import EventCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.parameter_type import ParameterType
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.realtime import (
    AppSettings,
    ClientState,
    EnterRoomParams,
    EventCaching,
    InRoomCallbacks,
    OnEventCallback,
    OperationResponseCallback,
    RaiseEventArgs,
    RealtimeClient,
    ReceiverGroup,
    SendOptions,
)
from pyphotonrealtime.realtime._convert import to_hashtable, to_python
from test_matchmaking import (
    ROOM,
    authenticate_on_game_server,
    b,
    enter_on_game_server,
    enter_on_master,
    run,
)

if TYPE_CHECKING:
    from pyphotonrealtime.peer import EventData, OperationResponse
    from pyphotonrealtime.protocol.enum_lookups import CommandParams
    from pyphotonrealtime.realtime import Player, Room

ME = 2
HOST = 1


class Recorder(InRoomCallbacks, OnEventCallback, OperationResponseCallback):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.events: list[EventData] = []
        self.responses: list[OperationResponse] = []

    @override
    def on_player_entered_room(self, player: Player) -> None:
        self.calls.append(("entered", player.actor_number))

    @override
    def on_player_left_room(self, player: Player) -> None:
        self.calls.append(("left", player.actor_number))

    @override
    def on_room_properties_update(self, changed: dict[Any, Any]) -> None:
        self.calls.append(("room_props", changed))

    @override
    def on_player_properties_update(
        self, player: Player, changed: dict[Any, Any]
    ) -> None:
        self.calls.append(("player_props", (player.actor_number, changed)))

    @override
    def on_master_client_switched(self, new_master: Player) -> None:
        self.calls.append(("master", new_master.actor_number))

    @override
    def on_event(self, event: EventData) -> None:
        self.events.append(event)

    @override
    def on_operation_response(self, response: OperationResponse) -> None:
        self.responses.append(response)


@pytest.fixture
def game_server() -> FakeServer:
    server = FakeServer("gs.example:4531")
    server.handlers[OperationCode.Authenticate] = authenticate_on_game_server
    server.handlers[OperationCode.JoinGame] = enter_on_game_server
    return server


@pytest.fixture
def cloud(game_server: FakeServer) -> FakeCloud:
    master = FakeServer("ms.example:4530")
    master.handlers[OperationCode.JoinGame] = enter_on_master
    return FakeCloud(FakeServer("ns.example:4533"), master, game_server)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def client(cloud: FakeCloud, recorder: Recorder) -> RealtimeClient:
    client = RealtimeClient(transport=cloud)
    client.add_callback_target(recorder)
    client.connect_using_settings(
        AppSettings(app_id_realtime=APP_ID, name_server="ns.example", fixed_region="eu")
    )
    run(client, ClientState.ConnectedToMasterServer)
    assert client.op_join_room(EnterRoomParams(room_name=ROOM))
    run(client, ClientState.Joined)
    recorder.calls.clear()
    recorder.events.clear()
    recorder.responses.clear()
    return client


def room_of(client: RealtimeClient) -> Room:
    assert client.current_room is not None
    return client.current_room


def deliver(
    client: RealtimeClient, cloud: FakeCloud, code: int, params: CommandParams
) -> None:
    cloud.push_event(code, params)
    client.service()


def int_array(*values: int) -> SliceParameter[Int32Parameter]:
    return SliceParameter(
        [Int32Parameter(v) for v in values], element_type=ParameterType.Int32Type
    )


# -- events from the Game Server -------------------------------------------


def test_join_event_adds_remote_player(
    client: RealtimeClient, cloud: FakeCloud, recorder: Recorder
) -> None:
    deliver(
        client,
        cloud,
        EventCode.Join,
        {
            ParameterKey.ActorNr: Int32Parameter(3),
            ParameterKey.PlayerProperties: to_hashtable({b(255): "bob", "hat": 1}),
            ParameterKey.ActorList: int_array(1, 2, 3),
        },
    )
    player = room_of(client).players[3]
    assert (player.nick_name, player.custom_properties) == ("bob", {"hat": 1})
    assert player.client is client
    assert recorder.calls == [("entered", 3)]
    assert recorder.events[-1].code == EventCode.Join


def test_own_join_event_is_not_reported(
    client: RealtimeClient, cloud: FakeCloud, recorder: Recorder
) -> None:
    deliver(
        client,
        cloud,
        EventCode.Join,
        {
            ParameterKey.ActorNr: Int32Parameter(ME),
            ParameterKey.ActorList: int_array(1, 2),
        },
    )
    assert recorder.calls == []
    assert room_of(client).players[ME] is client.local_player


def test_leave_event_removes_player_and_switches_master(
    client: RealtimeClient, cloud: FakeCloud, recorder: Recorder
) -> None:
    deliver(
        client,
        cloud,
        EventCode.Leave,
        {
            ParameterKey.ActorNr: Int32Parameter(HOST),
            ParameterKey.MasterClientId: Int32Parameter(ME),
        },
    )
    room = room_of(client)
    assert HOST not in room.players
    assert room.master_client is client.local_player
    assert recorder.calls == [("left", HOST), ("master", ME)]


def test_master_leaving_without_announcement_picks_lowest_actor(
    client: RealtimeClient, cloud: FakeCloud, recorder: Recorder
) -> None:
    deliver(
        client, cloud, EventCode.Leave, {ParameterKey.ActorNr: Int32Parameter(HOST)}
    )
    assert room_of(client).master_client_id == ME
    assert recorder.calls == [("left", HOST), ("master", ME)]


def test_inactive_player_stays_and_can_return(
    client: RealtimeClient, cloud: FakeCloud, recorder: Recorder
) -> None:
    deliver(
        client,
        cloud,
        EventCode.Leave,
        {
            ParameterKey.ActorNr: Int32Parameter(HOST),
            ParameterKey.IsInactive: BooleanParameter(value=True),
            ParameterKey.MasterClientId: Int32Parameter(ME),
        },
    )
    host = room_of(client).players[HOST]
    assert host.is_inactive
    deliver(client, cloud, EventCode.Join, {ParameterKey.ActorNr: Int32Parameter(HOST)})
    assert not host.is_inactive
    assert recorder.calls == [("left", HOST), ("master", ME), ("entered", HOST)]


def test_properties_changed_updates_room_and_players(
    client: RealtimeClient, cloud: FakeCloud, recorder: Recorder
) -> None:
    deliver(
        client,
        cloud,
        EventCode.PropertiesChanged,
        {
            ParameterKey.TargetActorNr: Int32Parameter(0),
            ParameterKey.Properties: to_hashtable(
                {"mode": None, "map": "dust", b(253): False}
            ),
        },
    )
    room = room_of(client)
    assert room.custom_properties == {"map": "dust"}
    assert not room.is_open
    deliver(
        client,
        cloud,
        EventCode.PropertiesChanged,
        {
            ParameterKey.TargetActorNr: Int32Parameter(HOST),
            ParameterKey.Properties: to_hashtable({"team": "blue"}),
        },
    )
    assert room.players[HOST].custom_properties == {"team": "blue"}
    assert recorder.calls == [
        ("room_props", {"mode": None, "map": "dust", 253: False}),
        ("player_props", (HOST, {"team": "blue"})),
    ]


def test_master_client_id_property_switches_master(
    client: RealtimeClient, cloud: FakeCloud, recorder: Recorder
) -> None:
    deliver(
        client,
        cloud,
        EventCode.PropertiesChanged,
        {
            ParameterKey.TargetActorNr: Int32Parameter(0),
            ParameterKey.Properties: to_hashtable({b(248): Int32Parameter(ME)}),
        },
    )
    assert room_of(client).master_client_id == ME
    assert recorder.calls[-1] == ("master", ME)


def test_custom_event_exposes_sender_and_data(
    client: RealtimeClient, cloud: FakeCloud, recorder: Recorder
) -> None:
    deliver(
        client,
        cloud,
        7,
        {
            ParameterKey.ActorNr: Int32Parameter(HOST),
            ParameterKey.Data: to_hashtable({"x": 1}),
        },
    )
    event = recorder.events[-1]
    assert (event.code, event.sender, event.custom_data) == (7, HOST, {"x": 1})


# -- operations ---------------------------------------------------------------


def test_raise_event_routing(client: RealtimeClient, game_server: FakeServer) -> None:
    assert client.op_raise_event(5, {"hp": 10})
    assert client.op_raise_event(
        6, [1], RaiseEventArgs(target_actors=[1], receivers=ReceiverGroup.All)
    )
    assert client.op_raise_event(7, None, RaiseEventArgs(interest_group=3))
    assert client.op_raise_event(
        8,
        "x",
        RaiseEventArgs(
            receivers=ReceiverGroup.All, caching=EventCaching.AddToRoomCache
        ),
        SendOptions(encrypt=True),
    )
    client.service()
    sent = game_server.ops(OperationCode.RaiseEvent)
    assert [to_python(op[ParameterKey.Code]) for op in sent] == [5, 6, 7, 8]
    assert to_python(sent[0][ParameterKey.Data]) == {"hp": 10}
    assert set(sent[0]) == {ParameterKey.Code, ParameterKey.Data}
    assert to_python(sent[1][ParameterKey.ActorList]) == [1]
    assert ParameterKey.ReceiverGroup not in sent[1]
    assert sent[2][ParameterKey.Group] == Int8Parameter(3)
    assert sent[3][ParameterKey.ReceiverGroup] == Int8Parameter(ReceiverGroup.All)
    assert sent[3][ParameterKey.Cache] == Int8Parameter(EventCaching.AddToRoomCache)
    assert game_server.operations[-1][2]  # encrypted


def test_raise_event_checks_code_and_room(client: RealtimeClient) -> None:
    with pytest.raises(ValueError, match="1-199"):
        client.op_raise_event(200, None)
    client.disconnect()
    assert not client.op_raise_event(1, None)


def test_change_groups(client: RealtimeClient, game_server: FakeServer) -> None:
    assert client.op_change_groups([], [1, 2])
    client.service()
    assert game_server.ops(OperationCode.ChangeGroups) == [
        {
            ParameterKey.Remove: Int8SliceParameter(b""),
            ParameterKey.Add: Int8SliceParameter(b"\x01\x02"),
        }
    ]


def test_set_room_properties_updates_locally(
    client: RealtimeClient, game_server: FakeServer
) -> None:
    room = room_of(client)
    assert room.set_custom_properties({"map": "dust", "mode": None})
    assert room.custom_properties == {"map": "dust"}
    client.service()
    (sent,) = game_server.ops(OperationCode.SetProperties)
    assert to_python(sent[ParameterKey.Properties]) == {"map": "dust", "mode": None}
    assert sent[ParameterKey.Broadcast] == BooleanParameter(value=True)
    assert ParameterKey.ExpectedValues not in sent
    assert ParameterKey.ActorNr not in sent


def test_compare_and_swap_waits_for_the_server(
    client: RealtimeClient, game_server: FakeServer
) -> None:
    room = room_of(client)
    assert room.set_custom_properties({"round": 2}, expected={"round": 1})
    assert "round" not in room.custom_properties
    client.service()
    (sent,) = game_server.ops(OperationCode.SetProperties)
    assert to_python(sent[ParameterKey.ExpectedValues]) == {"round": 1}


def test_set_master_client(client: RealtimeClient, game_server: FakeServer) -> None:
    room = room_of(client)
    assert room.set_master_client(client.local_player)
    assert room.master_client_id == HOST  # Until the server confirms.
    client.service()
    (sent,) = game_server.ops(OperationCode.SetProperties)
    assert sent[ParameterKey.Properties] == to_hashtable({b(248): Int32Parameter(ME)})
    assert sent[ParameterKey.ExpectedValues] == to_hashtable(
        {b(248): Int32Parameter(HOST)}
    )


def test_set_player_properties(client: RealtimeClient, game_server: FakeServer) -> None:
    assert client.local_player.set_custom_properties({"ready": True})
    assert client.local_player.custom_properties == {"ready": True}
    assert not client.op_set_properties_of_actor(99, {"x": 1})
    client.service()
    (sent,) = game_server.ops(OperationCode.SetProperties)
    assert sent[ParameterKey.ActorNr] == Int32Parameter(ME)


def test_op_custom_and_its_response(
    client: RealtimeClient, game_server: FakeServer, recorder: Recorder
) -> None:
    game_server.handlers[100] = lambda _request: (
        0,
        {ParameterKey.ClientKey: StringParameter("pong")},
    )
    assert client.op_custom(100, {1: "ping", 2: [1, 2]})
    client.service()  # Sends; the response is dispatched on the next call.
    client.service()
    (sent,) = game_server.ops(100)
    assert to_python(sent[ParameterKey.ClientKey]) == "ping"
    assert to_python(sent[ParameterKey.FindFriendsResponseRoomIdList]) == [1, 2]
    (response,) = recorder.responses
    assert response.operation_code == 100
    assert to_python(response.parameters[1]) == "pong"
