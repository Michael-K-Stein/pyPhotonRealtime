"""``RealtimeClient``: the high-level Photon Realtime workflow.

Owns one :class:`PhotonPeer` at a time and hops it across servers:

    Name Server  --Authenticate-->  (master address)
    Master Server --Join/Create--> (game server address + room)
    Game Server  --Join/Create-->  in room: events, properties, players

Like the official SDK, it is driven entirely by :meth:`service`; call it
regularly from one thread. Callbacks fire from inside ``service``.
"""

from typing import Any, Dict, List, Optional

from pyphotonrealtime.peer import (
    EventData,
    OperationResponse,
    PhotonPeer,
    StatusCode,
)
from pyphotonrealtime.realtime.options import (
    EnterRoomParams,
    RaiseEventArgs,
    SendOptions,
)
from pyphotonrealtime.realtime.player import Player
from pyphotonrealtime.realtime.room import Room, RoomInfo
from pyphotonrealtime.realtime.settings import AppSettings
from pyphotonrealtime.realtime.state import ClientState, DisconnectCause, ServerType


class RealtimeClient:
    def __init__(self) -> None:
        self.state = ClientState.PeerCreated
        self.server = ServerType.NameServer
        self.disconnect_cause = DisconnectCause.NoCause
        self.settings: Optional[AppSettings] = None

        self.peer = PhotonPeer(listener=self)
        self.local_player = Player(actor_number=-1, is_local=True)
        self.current_room: Optional[Room] = None
        self.room_list: Dict[str, RoomInfo] = {}

        self.user_id: Optional[str] = None
        self.cloud_region: Optional[str] = None
        self.master_server_address: Optional[str] = None
        self.game_server_address: Optional[str] = None

        self._callback_targets: List[Any] = []

    # -- callbacks --------------------------------------------------------

    def add_callback_target(self, target: Any) -> None:
        """Register an object implementing any of the ``*Callbacks`` classes."""
        if target not in self._callback_targets:
            self._callback_targets.append(target)

    def remove_callback_target(self, target: Any) -> None:
        if target in self._callback_targets:
            self._callback_targets.remove(target)

    def _emit(self, name: str, *args: Any) -> None:
        for target in list(self._callback_targets):
            handler = getattr(target, name, None)
            if handler is not None:
                handler(*args)

    # -- service loop -----------------------------------------------------

    def service(self) -> None:
        self.peer.service()

    # -- connection -------------------------------------------------------

    def connect_using_settings(self, settings: AppSettings) -> bool:
        """TODO: Name Server (or direct Master if ``use_name_server`` is False)."""
        raise NotImplementedError

    def connect_to_region_master(self, region: str) -> bool:
        raise NotImplementedError

    def reconnect_and_rejoin(self) -> bool:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    @property
    def is_connected_and_ready(self) -> bool:
        return self.state in (
            ClientState.ConnectedToMasterServer,
            ClientState.JoinedLobby,
            ClientState.Joined,
        )

    @property
    def in_room(self) -> bool:
        return self.state == ClientState.Joined and self.current_room is not None

    # -- matchmaking (Master Server) ---------------------------------------

    def op_join_lobby(self, lobby: Optional[str] = None) -> bool:
        raise NotImplementedError

    def op_leave_lobby(self) -> bool:
        raise NotImplementedError

    def op_create_room(self, params: EnterRoomParams) -> bool:
        raise NotImplementedError

    def op_join_room(self, params: EnterRoomParams) -> bool:
        raise NotImplementedError

    def op_join_or_create_room(self, params: EnterRoomParams) -> bool:
        raise NotImplementedError

    def op_join_random_room(
        self,
        expected_properties: Optional[Dict[Any, Any]] = None,
        expected_max_players: int = 0,
    ) -> bool:
        raise NotImplementedError

    def op_rejoin_room(self, room_name: str) -> bool:
        raise NotImplementedError

    def op_find_friends(self, user_ids: List[str]) -> bool:
        raise NotImplementedError

    def op_get_regions(self) -> bool:
        raise NotImplementedError

    # -- in room (Game Server) --------------------------------------------

    def op_leave_room(self, become_inactive: bool = False) -> bool:
        raise NotImplementedError

    def op_raise_event(
        self,
        event_code: int,
        content: Any,
        args: Optional[RaiseEventArgs] = None,
        send_options: Optional[SendOptions] = None,
    ) -> bool:
        raise NotImplementedError

    def op_change_groups(
        self, remove: Optional[List[int]], add: Optional[List[int]]
    ) -> bool:
        raise NotImplementedError

    def op_set_properties_of_room(
        self,
        properties: Dict[Any, Any],
        expected: Optional[Dict[Any, Any]] = None,
    ) -> bool:
        raise NotImplementedError

    def op_set_properties_of_actor(
        self, actor_number: int, properties: Dict[Any, Any]
    ) -> bool:
        raise NotImplementedError

    def op_custom(
        self,
        operation_code: int,
        parameters: Dict[int, Any],
        send_options: Optional[SendOptions] = None,
    ) -> bool:
        """Escape hatch for game-specific or undocumented operations."""
        raise NotImplementedError

    # -- PeerListener -----------------------------------------------------

    def on_status_changed(self, status: StatusCode) -> None:
        """TODO: drive the state machine (server hops, disconnect causes)."""

    def on_operation_response(self, response: OperationResponse) -> None:
        """TODO: Authenticate / Join / Create / SetProperties / GetRegions."""

    def on_event(self, event: EventData) -> None:
        """TODO: Join / Leave / PropertiesChanged / GameList, then forward."""
        self._emit("on_event", event)
