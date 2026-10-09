"""``RealtimeClient``: the high-level Photon Realtime workflow.

Owns one :class:`PhotonPeer` at a time and hops it across servers::

    Name Server   --Authenticate-->  master address
    Master Server --Join/Create--->  game server address + room
    Game Server   --Join/Create--->  in room: events, properties, players

Like the official SDK, it is driven entirely by :meth:`RealtimeClient.service`;
call it regularly from one thread. Callbacks fire from inside ``service``.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, cast

from pyphotonrealtime.peer import PeerState, PhotonPeer, StatusCode
from pyphotonrealtime.protocol.event_code import CUSTOM_EVENT_CODE_MAX, EventCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.parameter_type import ParameterType
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.protocol.property_keys import ActorPropertyKey, GamePropertyKey
from pyphotonrealtime.realtime._convert import (
    string_array,
    to_hashtable,
    to_param,
    to_python,
)
from pyphotonrealtime.realtime.authentication import (
    AuthenticationValues,
    AuthMode,
    CustomAuthenticationType,
)
from pyphotonrealtime.realtime.error_code import ErrorCode
from pyphotonrealtime.realtime.lobby import (
    FriendInfo,
    JoinMode,
    LobbyStatistics,
    LobbyType,
    MatchmakingMode,
    TypedLobby,
)
from pyphotonrealtime.realtime.options import (
    EnterRoomParams,
    EventCaching,
    RaiseEventArgs,
    ReceiverGroup,
    RoomOptions,
)
from pyphotonrealtime.realtime.player import Player
from pyphotonrealtime.realtime.region import Region, RegionPinger
from pyphotonrealtime.realtime.room import Room, RoomInfo
from pyphotonrealtime.realtime.settings import DEFAULT_MASTER_SERVER_PORT_TCP
from pyphotonrealtime.realtime.state import ClientState, DisconnectCause, ServerType

if TYPE_CHECKING:
    from pyphotonrealtime.peer import EventData, OperationResponse, Parameters
    from pyphotonrealtime.protocol.param.base import ParameterBase
    from pyphotonrealtime.realtime.callbacks import CallbackTarget
    from pyphotonrealtime.realtime.options import SendOptions
    from pyphotonrealtime.realtime.settings import AppSettings
    from pyphotonrealtime.transport import Transport

# ConnectionProtocol.Tcp and EncryptionMode.PayloadEncryption, sent with AuthOnce.
_PROTOCOL_TCP = 1
_PAYLOAD_ENCRYPTION = 0
# Keys of the EncryptionData dictionary in the AuthOnce response.
_ENCRYPTION_SECRET1 = 1
_BYTE_MAX = 0xFF

_AUTH_FAILURE_CAUSES: dict[int, DisconnectCause] = {
    ErrorCode.InvalidAuthentication: DisconnectCause.InvalidAuthentication,
    ErrorCode.CustomAuthenticationFailed: DisconnectCause.CustomAuthenticationFailed,
    ErrorCode.AuthenticationTicketExpired: DisconnectCause.AuthenticationTicketExpired,
    ErrorCode.MaxCcuReached: DisconnectCause.MaxCcuReached,
    ErrorCode.InvalidRegion: DisconnectCause.InvalidRegion,
    ErrorCode.OperationNotAllowedInCurrentState: (
        DisconnectCause.OperationNotAllowedInCurrentState
    ),
}

_STATUS_CAUSES = {
    StatusCode.ExceptionOnConnect: DisconnectCause.ExceptionOnConnect,
    StatusCode.Exception: DisconnectCause.Exception,
    StatusCode.TimeoutDisconnect: DisconnectCause.ClientTimeout,
    StatusCode.DisconnectByServer: DisconnectCause.DisconnectByServerReasonUnknown,
}

_CONNECTING_STATES = {
    ServerType.NameServer: ClientState.ConnectingToNameServer,
    ServerType.MasterServer: ClientState.ConnectingToMasterServer,
    ServerType.GameServer: ClientState.ConnectingToGameServer,
}

_MATCHMAKING_STATES = {ClientState.ConnectedToMasterServer, ClientState.JoinedLobby}

_ENTER_FAILED_CALLBACKS = {
    OperationCode.CreateGame: "on_create_room_failed",
    OperationCode.JoinGame: "on_join_room_failed",
    OperationCode.JoinRandomGame: "on_join_random_failed",
}


def _byte_key(member: GamePropertyKey | ActorPropertyKey) -> Int8Parameter:
    return cast("Int8Parameter", member.value)


@dataclasses.dataclass(slots=True)
class _EnterRoom:
    """A Create/Join sent to the Master Server, then repeated on the Game Server."""

    operation: OperationCode
    params: EnterRoomParams
    join_mode: JoinMode = JoinMode.Default
    failed_callback: str = ""

    def __post_init__(self) -> None:
        self.failed_callback = (
            self.failed_callback or (_ENTER_FAILED_CALLBACKS[self.operation])
        )


class RealtimeClient:
    """Connects to Photon, matchmakes, and tracks the current room."""

    def __init__(self, transport: Transport | None = None) -> None:
        """Create a client; nothing touches the network until ``connect_*``.

        Args:
            transport: Byte transport for the peer; defaults to TCP.
        """
        self.state = ClientState.PeerCreated
        self.server = ServerType.NameServer
        self.disconnect_cause = DisconnectCause.NoCause
        self.settings: AppSettings | None = None
        self.auth_values: AuthenticationValues | None = None
        """Set before connecting to send a user id or use custom authentication."""

        self.peer = PhotonPeer(listener=self, transport=transport)
        self.local_player = Player(actor_number=-1, is_local=True, client=self)
        self.current_room: Room | None = None
        self.room_list: dict[str, RoomInfo] = {}
        """Rooms listed in the current lobby, by name."""
        self.current_lobby = TypedLobby()
        self.in_lobby = False
        self.lobby_statistics: list[LobbyStatistics] = []
        self.players_on_master_count = 0
        self.players_in_rooms_count = 0
        self.rooms_count = 0

        self.user_id: str | None = None
        self.cloud_region: str | None = None
        self.current_cluster: str | None = None
        self.regions: list[Region] = []
        self.name_server_address: str | None = None
        self.master_server_address: str | None = None
        self.game_server_address: str | None = None

        self._callback_targets: list[CallbackTarget] = []
        self._token: ParameterBase[Any] | None = None
        self._payload_secret: bytes | None = None
        self._next_hop: tuple[ServerType, str] | None = None
        self._pinger: RegionPinger | None = None
        self._announced_connect = False
        self._enter: _EnterRoom | None = None
        self._last_room: str | None = None
        self._friends_requested: list[str] = []

    # -- callbacks --------------------------------------------------------

    def add_callback_target(self, target: CallbackTarget) -> None:
        """Register an object implementing any of the ``*Callbacks`` classes."""
        if target not in self._callback_targets:
            self._callback_targets.append(target)

    def remove_callback_target(self, target: CallbackTarget) -> None:
        """Unregister a callback target; unknown targets are ignored."""
        if target in self._callback_targets:
            self._callback_targets.remove(target)

    def _emit(self, name: str, *args: object) -> None:
        for target in list(self._callback_targets):
            handler = getattr(target, name, None)
            if handler is not None:
                handler(*args)

    # -- service loop -----------------------------------------------------

    def service(self) -> None:
        """Pump the network and fire callbacks. Call 10-50 times per second."""
        self.peer.service()
        if self._pinger is not None and self._pinger.poll():
            self._on_regions_pinged(self._pinger)

    # -- connection -------------------------------------------------------

    def connect_using_settings(self, settings: AppSettings) -> bool:
        """Connect to the Name Server (or straight to a Master Server).

        Without ``fixed_region`` the client fetches the region list, pings
        every region and picks the closest one.

        Returns:
            Whether the connection attempt was started.

        Raises:
            ValueError: ``use_name_server`` is False but ``server`` is unset.
        """
        if self.peer.state != PeerState.Disconnected:
            return False
        self.settings = settings
        self.cloud_region = settings.fixed_region
        self.regions = []
        self._token = None
        self._payload_secret = None
        if self.auth_values is not None:
            self.auth_values.token = None
        if settings.use_name_server:
            self.name_server_address = (
                f"{settings.name_server}:{settings.name_server_port}"
            )
            return self._start_connection(
                ServerType.NameServer, self.name_server_address
            )
        if not settings.server:
            msg = "use_name_server=False needs AppSettings.server"
            raise ValueError(msg)
        port = settings.port or DEFAULT_MASTER_SERVER_PORT_TCP
        self.master_server_address = f"{settings.server}:{port}"
        return self._start_connection(
            ServerType.MasterServer, self.master_server_address
        )

    def connect_to_region_master(self, region: str) -> bool:
        """Connect via the Name Server to the Master Server of ``region``.

        Uses the settings of the previous ``connect_using_settings``.

        Returns:
            Whether the connection attempt was started.
        """
        if self.settings is None:
            return False
        return self.connect_using_settings(
            dataclasses.replace(
                self.settings, fixed_region=region, use_name_server=True
            )
        )

    def reconnect_to_master(self) -> bool:
        """Reconnect to the last Master Server, reusing the authentication token.

        Returns:
            Whether the attempt was started; False without a previous session.
        """
        if (
            self.peer.state != PeerState.Disconnected
            or self.master_server_address is None
            or self._token is None
        ):
            return False
        return self._start_connection(
            ServerType.MasterServer, self.master_server_address
        )

    def reconnect_and_rejoin(self) -> bool:
        """Reconnect to the last Game Server and rejoin the last room.

        Meant for unexpected disconnects from a room with a ``player_ttl``:
        the slot is kept while the player is inactive.

        Returns:
            Whether the attempt was started; False without a previous room.
        """
        if (
            self.peer.state != PeerState.Disconnected
            or self.game_server_address is None
            or self._last_room is None
            or self._token is None
        ):
            return False
        self._enter = _EnterRoom(
            OperationCode.JoinGame,
            EnterRoomParams(room_name=self._last_room),
            JoinMode.RejoinOnly,
        )
        return self._start_connection(ServerType.GameServer, self.game_server_address)

    def disconnect(self) -> None:
        """Leave any room and disconnect from Photon."""
        if self.state in {
            ClientState.PeerCreated,
            ClientState.Disconnecting,
            ClientState.Disconnected,
        }:
            return
        self._last_room = None
        self._fail(DisconnectCause.DisconnectByClientLogic)

    @property
    def auth_mode(self) -> AuthMode:
        """The authentication mode of the current settings."""
        return self.settings.auth_mode if self.settings else AuthMode.Auth

    @property
    def is_connected_and_ready(self) -> bool:
        """Whether operations can be sent right now."""
        return self.state in {
            ClientState.ConnectedToMasterServer,
            ClientState.JoinedLobby,
            ClientState.Joined,
        }

    @property
    def in_room(self) -> bool:
        """Whether the client is inside a room."""
        return self.state == ClientState.Joined and self.current_room is not None

    # -- matchmaking (Master Server) ---------------------------------------

    def op_join_lobby(self, lobby: TypedLobby | None = None) -> bool:
        """Join ``lobby`` (the default lobby if None) to receive room lists.

        Returns:
            Whether the operation was queued.
        """
        lobby = lobby or TypedLobby()
        if not self._can_matchmake() or not self.peer.send_operation(
            OperationCode.JoinLobby, _lobby_params(lobby)
        ):
            return False
        self.current_lobby = lobby
        self.state = ClientState.JoiningLobby
        return True

    def op_leave_lobby(self) -> bool:
        """Leave the current lobby.

        Returns:
            Whether the operation was queued.
        """
        if self.state != ClientState.JoinedLobby:
            return False
        return self.peer.send_operation(OperationCode.LeaveLobby, {})

    def op_get_game_list(self, lobby: TypedLobby, sql_filter: str) -> bool:
        """Query the rooms of a SQL lobby; the result is an ``on_room_list_update``.

        Returns:
            Whether the operation was queued.
        """
        if not self._can_matchmake() or lobby.type != LobbyType.SqlLobby:
            return False
        params = _lobby_params(lobby)
        params[ParameterKey.SqlLobbyFilter] = StringParameter(sql_filter)
        return self.peer.send_operation(OperationCode.GetGameList, params)

    def op_create_room(self, params: EnterRoomParams) -> bool:
        """Create a room and join it.

        Returns:
            Whether the operation was queued.
        """
        return self._enter_room(_EnterRoom(OperationCode.CreateGame, params))

    def op_join_room(self, params: EnterRoomParams) -> bool:
        """Join an existing room by name.

        Returns:
            Whether the operation was queued.
        """
        if not params.room_name:
            return False
        return self._enter_room(_EnterRoom(OperationCode.JoinGame, params))

    def op_join_or_create_room(self, params: EnterRoomParams) -> bool:
        """Join a room by name, creating it if it doesn't exist.

        Returns:
            Whether the operation was queued.
        """
        if not params.room_name:
            return False
        return self._enter_room(
            _EnterRoom(OperationCode.JoinGame, params, JoinMode.CreateIfNotExists)
        )

    def op_join_random_room(  # noqa: PLR0913
        self,
        expected_properties: dict[Any, Any] | None = None,
        expected_max_players: int = 0,
        *,
        matchmaking_mode: MatchmakingMode = MatchmakingMode.FillRoom,
        lobby: TypedLobby | None = None,
        sql_lobby_filter: str | None = None,
        expected_users: list[str] | None = None,
    ) -> bool:
        """Join any open room matching the filter.

        Args:
            expected_properties: Custom room properties the room must have; they
                must be in the room's ``custom_room_properties_for_lobby``.
            expected_max_players: Required ``max_players``; 0 accepts any.
            matchmaking_mode: Which of the matching rooms to prefer.
            lobby: Lobby to search; None uses the current lobby.
            sql_lobby_filter: SQL ``WHERE`` clause for a SQL lobby, e.g. ``"C0 > 3"``.
            expected_users: Users to reserve slots for in the joined room.

        Returns:
            Whether the operation was queued.
        """
        properties = {
            to_param(k): to_param(v) for k, v in (expected_properties or {}).items()
        }
        if expected_max_players > 0:
            properties[_byte_key(GamePropertyKey.MaxPlayers)] = Int8Parameter(
                min(expected_max_players, _BYTE_MAX)
            )
        params: Parameters = {}
        if properties:
            params[ParameterKey.GameProperties] = to_hashtable(properties)
        if matchmaking_mode != MatchmakingMode.FillRoom:
            params[ParameterKey.MatchMakingType] = Int8Parameter(matchmaking_mode)
        params.update(_lobby_params(lobby or self.current_lobby))
        if sql_lobby_filter:
            params[ParameterKey.SqlLobbyFilter] = StringParameter(sql_lobby_filter)
        if expected_users:
            params[ParameterKey.Add] = string_array(expected_users)
        enter = _EnterRoom(
            OperationCode.JoinRandomGame,
            EnterRoomParams(lobby=lobby, expected_users=expected_users),
        )
        return self._send_enter_room(enter, params)

    def op_rejoin_room(self, room_name: str) -> bool:
        """Return to a room this client was inactive in (``player_ttl``).

        Returns:
            Whether the operation was queued.
        """
        return self._enter_room(
            _EnterRoom(
                OperationCode.JoinGame,
                EnterRoomParams(room_name=room_name),
                JoinMode.RejoinOnly,
            )
        )

    def op_find_friends(self, user_ids: list[str]) -> bool:
        """Ask which of ``user_ids`` are online and in which rooms.

        Only on the Master Server; the answer is an ``on_friend_list_update``.

        Returns:
            Whether the operation was queued.
        """
        if (
            not user_ids
            or not self._can_matchmake()
            or not self.peer.send_operation(
                OperationCode.FindFriends,
                {ParameterKey.FindFriendsRequestList: string_array(user_ids)},
            )
        ):
            return False
        self._friends_requested = list(user_ids)
        return True

    def op_get_regions(self) -> bool:
        """Ask the Name Server for the list of regions.

        Returns:
            Whether the operation was queued.
        """
        if self.server != ServerType.NameServer or self.settings is None:
            return False
        return self.peer.send_operation(
            OperationCode.GetRegions,
            {
                ParameterKey.ApplicationId: StringParameter(
                    self.settings.app_id_realtime
                )
            },
        )

    # -- in room (Game Server) --------------------------------------------

    def op_leave_room(self, *, become_inactive: bool = False) -> bool:
        """Leave the room; ``become_inactive`` keeps the slot for a rejoin.

        The client then returns to the Master Server (``on_connected_to_master``).

        Returns:
            Whether the operation was queued.
        """
        params: Parameters = {}
        if become_inactive:
            params[ParameterKey.IsInactive] = BooleanParameter(value=True)
        if not self.in_room or not self.peer.send_operation(
            OperationCode.Leave, params
        ):
            return False
        if not become_inactive:
            self._last_room = None
        self.state = ClientState.Leaving
        return True

    def op_raise_event(
        self,
        event_code: int,
        content: object,
        args: RaiseEventArgs | None = None,
        send_options: SendOptions | None = None,
    ) -> bool:
        """Send a custom event (codes 1-199) to other players in the room.

        Receivers get it as an ``on_event`` with ``event.code == event_code``;
        ``event.custom_data`` is ``content`` and ``event.sender`` this client.

        Args:
            event_code: The event code, 1-199.
            content: Any value ``to_param`` accepts (dicts, lists, scalars).
            args: Receivers, interest group and caching; defaults to others.
            send_options: Delivery options; only ``encrypt`` applies to TCP.

        Returns:
            Whether the operation was queued.

        Raises:
            ValueError: ``event_code`` is outside 1-199.
        """
        if not 0 < event_code < CUSTOM_EVENT_CODE_MAX:
            msg = f"Custom event codes are 1-199, got {event_code}"
            raise ValueError(msg)
        args = args or RaiseEventArgs()
        params: Parameters = {
            ParameterKey.Code: Int8Parameter(event_code),
            ParameterKey.Data: to_param(content),
        }
        if args.caching != EventCaching.DoNotCache:
            params[ParameterKey.Cache] = Int8Parameter(args.caching)
        # Like the SDKs: explicit targets win over a group, a group over receivers.
        if args.target_actors is not None:
            params[ParameterKey.ActorList] = _int_array(args.target_actors)
        elif args.interest_group:
            params[ParameterKey.Group] = Int8Parameter(args.interest_group)
        elif args.receivers != ReceiverGroup.Others:
            params[ParameterKey.ReceiverGroup] = Int8Parameter(args.receivers)
        return self._send_in_room(OperationCode.RaiseEvent, params, send_options)

    def op_change_groups(self, remove: list[int] | None, add: list[int] | None) -> bool:
        """Change which interest groups this client receives events from.

        ``None`` leaves that side unchanged; an empty list means all groups.
        The server removes before it adds.

        Returns:
            Whether the operation was queued.
        """
        params: Parameters = {}
        if remove is not None:
            params[ParameterKey.Remove] = Int8SliceParameter(bytes(remove))
        if add is not None:
            params[ParameterKey.Add] = Int8SliceParameter(bytes(add))
        return self._send_in_room(OperationCode.ChangeGroups, params)

    def op_set_properties_of_room(
        self,
        properties: dict[Any, Any],
        expected: dict[Any, Any] | None = None,
    ) -> bool:
        """Set room properties, optionally only if ``expected`` still holds.

        String keys are custom properties (a ``None`` value deletes one); use an
        ``Int8Parameter`` key for a well-known one, e.g. ``IsOpen``. Without
        ``expected`` the local room updates right away. With it, the server
        applies the change only if every expected value matches (compare and
        swap), and the room updates when the ``PropertiesChanged`` event arrives.

        Returns:
            Whether the operation was queued.
        """
        room = self.current_room
        if room is None or not properties:
            return False
        if not self._set_properties(properties, expected):
            return False
        if not expected:
            room.update(to_python(to_hashtable(properties)))
        return True

    def op_set_properties_of_actor(
        self,
        actor_number: int,
        properties: dict[Any, Any],
        expected: dict[Any, Any] | None = None,
    ) -> bool:
        """Set a player's properties, optionally only if ``expected`` still holds.

        Keys and ``expected`` work as in :meth:`op_set_properties_of_room`.

        Returns:
            Whether the operation was queued.
        """
        room = self.current_room
        player = room.players.get(actor_number) if room is not None else None
        if player is None or not properties:
            return False
        if not self._set_properties(properties, expected, actor_number=actor_number):
            return False
        if not expected:
            player.update(to_python(to_hashtable(properties)))
        return True

    def op_custom(
        self,
        operation_code: int,
        parameters: dict[int, Any],
        send_options: SendOptions | None = None,
    ) -> bool:
        """Send any operation; the escape hatch for game-specific ops.

        Values are converted with ``to_param``. The response goes to callback
        targets implementing ``OperationResponseCallback``.

        Returns:
            Whether the operation was queued.
        """
        encrypt = send_options is not None and send_options.encrypt
        return self.peer.send_operation(
            operation_code,
            {key: to_param(value) for key, value in parameters.items()},
            encrypt=encrypt,
        )

    def _send_in_room(
        self,
        operation: OperationCode,
        params: Parameters,
        send_options: SendOptions | None = None,
    ) -> bool:
        if not self.in_room:
            return False
        encrypt = send_options is not None and send_options.encrypt
        return self.peer.send_operation(operation, params, encrypt=encrypt)

    def _set_properties(
        self,
        properties: dict[Any, Any],
        expected: dict[Any, Any] | None,
        *,
        actor_number: int | None = None,
    ) -> bool:
        params: Parameters = {
            ParameterKey.Properties: to_hashtable(properties),
            ParameterKey.Broadcast: BooleanParameter(value=True),
        }
        if actor_number is not None:
            params[ParameterKey.ActorNr] = Int32Parameter(actor_number)
        if expected:
            params[ParameterKey.ExpectedValues] = to_hashtable(expected)
        return self._send_in_room(OperationCode.SetProperties, params)

    # -- PeerListener -----------------------------------------------------

    def on_status_changed(self, status: StatusCode) -> None:
        """Drive the state machine from peer status changes (server hops)."""
        match status:
            case StatusCode.Connect:
                self._on_connect()
            case StatusCode.EncryptionEstablished:
                if self.server == ServerType.NameServer and not self.cloud_region:
                    self.op_get_regions()
                else:
                    self._authenticate()
            case StatusCode.EncryptionFailedToEstablish:
                self._fail(DisconnectCause.Exception)
            case StatusCode.Disconnect if self._next_hop is not None:
                server, address = self._next_hop
                self._next_hop = None
                self._start_connection(server, address)
            case _ if self.state == ClientState.Disconnected:
                pass  # Already reported, e.g. by disconnect() after a hop.
            case _:
                if self.disconnect_cause == DisconnectCause.NoCause:
                    self.disconnect_cause = _STATUS_CAUSES.get(
                        status, DisconnectCause.NoCause
                    )
                self._set_disconnected()

    def on_operation_response(self, response: OperationResponse) -> None:
        """Handle Authenticate / Join / Create / SetProperties / GetRegions."""
        match response.operation_code:
            case OperationCode.Authenticate | OperationCode.AuthOnce:
                self._on_authenticate(response)
            case OperationCode.GetRegions:
                self._on_get_regions(response)
            case OperationCode.JoinLobby:
                self._on_joined_lobby(response)
            case OperationCode.LeaveLobby:
                self._on_left_lobby()
            case OperationCode.GetGameList:
                self._on_game_list(response.parameters, full=False)
            case OperationCode.FindFriends:
                self._on_find_friends(response)
            case (
                OperationCode.CreateGame
                | OperationCode.JoinGame
                | OperationCode.JoinRandomGame
            ):
                self._on_enter_room(response)
            case OperationCode.Leave if self.state == ClientState.Leaving:
                self._leave_game_server()
            case _:
                pass
        self._emit("on_operation_response", response)

    def on_event(self, event: EventData) -> None:
        """Update room state from built-in events, then forward to targets."""
        match event.code:
            case EventCode.GameList:
                self._on_game_list(event.parameters, full=True)
            case EventCode.GameListUpdate:
                self._on_game_list(event.parameters, full=False)
            case EventCode.LobbyStats:
                self._on_lobby_stats(event.parameters)
            case EventCode.AppStats:
                self._on_app_stats(event.parameters)
            case EventCode.Join if self.current_room is not None:
                self._on_join_event(self.current_room, event.parameters)
            case EventCode.Leave if self.current_room is not None:
                self._on_leave_event(self.current_room, event.parameters)
            case EventCode.PropertiesChanged if self.current_room is not None:
                self._on_properties_changed(self.current_room, event.parameters)
            case _:
                pass
        self._emit("on_event", event)

    # -- connection workflow ------------------------------------------------

    def _start_connection(self, server: ServerType, address: str) -> bool:
        if self.state in {ClientState.PeerCreated, ClientState.Disconnected}:
            self.disconnect_cause = DisconnectCause.NoCause
            self._announced_connect = False
        assert self.settings is not None  # noqa: S101 -- set by every connect_*
        self.server = server
        self.state = _CONNECTING_STATES[server]
        return self.peer.connect(address, self.settings.app_id_realtime)

    def _on_connect(self) -> None:
        if self.server == ServerType.NameServer:
            self.state = ClientState.ConnectedToNameServer
        elif self.server == ServerType.GameServer:
            self.state = ClientState.ConnectedToGameServer
        if not self._announced_connect:
            self._announced_connect = True
            self._emit("on_connected")
        if (
            self.auth_mode == AuthMode.AuthOnce
            and self.server != ServerType.NameServer
            and self._token is not None
        ):
            # The Name Server already handed out the key and a token.
            if self._payload_secret is not None:
                self.peer.init_payload_encryption(self._payload_secret)
            self._authenticate()
        else:
            self.peer.establish_encryption()

    def _authenticate(self) -> None:
        assert self.settings is not None  # noqa: S101 -- set by every connect_*
        settings = self.settings
        self.state = ClientState.Authenticating
        if self.server != ServerType.NameServer and self._token is not None:
            self.peer.send_operation(
                OperationCode.Authenticate, {ParameterKey.Token: self._token}
            )
            return

        params: Parameters = {
            ParameterKey.AppVersion: StringParameter(settings.app_version),
            ParameterKey.ApplicationId: StringParameter(settings.app_id_realtime),
        }
        if self.server == ServerType.NameServer and self.cloud_region:
            params[ParameterKey.Region] = StringParameter(self.cloud_region)
        if self.auth_values is not None:
            params.update(_auth_value_params(self.auth_values))
        if settings.enable_lobby_statistics:
            params[ParameterKey.LobbyStats] = BooleanParameter(value=True)

        operation = OperationCode.Authenticate
        if self.server == ServerType.NameServer and self.auth_mode == AuthMode.AuthOnce:
            operation = OperationCode.AuthOnce
            params[ParameterKey.ExpectedProtocol] = Int8Parameter(_PROTOCOL_TCP)
            params[ParameterKey.EncryptionMode] = Int8Parameter(_PAYLOAD_ENCRYPTION)
        self.peer.send_operation(operation, params, encrypt=True)

    def _on_authenticate(self, response: OperationResponse) -> None:
        if response.return_code != ErrorCode.Ok:
            if response.return_code == ErrorCode.CustomAuthenticationFailed:
                self._emit(
                    "on_custom_authentication_failed", response.debug_message or ""
                )
            self._fail_operation(response.return_code)
            return

        self._store_authentication(response.parameters)
        if self.server == ServerType.GameServer:
            self._send_enter_room_to_game_server()
            return
        if self.server == ServerType.MasterServer:
            self.state = ClientState.ConnectedToMasterServer
            self.in_lobby = False
            self._emit("on_connected_to_master")
            return

        params = response.parameters
        if (cluster := params.get(ParameterKey.Cluster)) is not None:
            self.current_cluster = str(cluster.value)
        if (encryption := params.get(ParameterKey.EncryptionData)) is not None:
            secret = to_python(encryption).get(_ENCRYPTION_SECRET1)
            self._payload_secret = secret if isinstance(secret, bytes) else None
        address = params.get(ParameterKey.Address)
        if address is None:
            self._fail(DisconnectCause.DisconnectByServerLogic)
            return
        self.master_server_address = str(address.value)
        self.state = ClientState.DisconnectingFromNameServer
        self._next_hop = (ServerType.MasterServer, self.master_server_address)
        self.peer.disconnect()

    def _store_authentication(self, params: Parameters) -> None:
        if (token := params.get(ParameterKey.Token)) is not None:
            self._token = token
            if self.auth_values is None:
                self.auth_values = AuthenticationValues()
            self.auth_values.token = to_python(token)
        if (user_id := params.get(ParameterKey.UserId)) is not None:
            self.user_id = self.local_player.user_id = str(user_id.value)
        if (nickname := params.get(ParameterKey.Nickname)) is not None:
            self.local_player.nick_name = str(nickname.value)
        if (data := params.get(ParameterKey.Data)) is not None:
            self._emit("on_custom_authentication_response", to_python(data))

    def _on_get_regions(self, response: OperationResponse) -> None:
        params = response.parameters
        codes = params.get(ParameterKey.Region)
        addresses = params.get(ParameterKey.Address)
        if response.return_code != ErrorCode.Ok or codes is None or addresses is None:
            self._fail_operation(response.return_code)
            return
        self.regions = [
            Region.from_name_server(code, address)
            for code, address in zip(
                to_python(codes), to_python(addresses), strict=True
            )
        ]
        self._emit("on_region_list_received", {r.code: r.address for r in self.regions})
        if not self.cloud_region and self.state == ClientState.ConnectedToNameServer:
            self._pinger = RegionPinger(self.regions)

    def _on_regions_pinged(self, pinger: RegionPinger) -> None:
        self._pinger = None
        best = pinger.best_region
        if best is None:  # No Master Server answered.
            self._fail(DisconnectCause.ExceptionOnConnect)
            return
        self.cloud_region = best.code
        self._authenticate()

    # -- lobbies (Master Server) ---------------------------------------------

    def _can_matchmake(self) -> bool:
        return (
            self.server == ServerType.MasterServer and self.state in _MATCHMAKING_STATES
        )

    def _master_state(self) -> ClientState:
        if self.in_lobby:
            return ClientState.JoinedLobby
        return ClientState.ConnectedToMasterServer

    def _on_joined_lobby(self, response: OperationResponse) -> None:
        if response.return_code != ErrorCode.Ok:
            self.state = self._master_state()
            return
        self.in_lobby = True
        self.room_list = {}
        self.state = ClientState.JoinedLobby
        self._emit("on_joined_lobby")

    def _on_left_lobby(self) -> None:
        self.in_lobby = False
        self.room_list = {}
        if self.state == ClientState.JoinedLobby:
            self.state = ClientState.ConnectedToMasterServer
        self._emit("on_left_lobby")

    def _on_game_list(self, params: Parameters, *, full: bool) -> None:
        game_list = params.get(ParameterKey.GameList)
        if game_list is None:
            return
        if full:
            self.room_list = {}
        changed: list[RoomInfo] = []
        for name, properties in to_python(game_list).items():
            room = self.room_list.get(name) or RoomInfo(str(name))
            room.update(properties)
            if room.removed_from_list:
                self.room_list.pop(room.name, None)
            else:
                self.room_list[room.name] = room
            changed.append(room)
        self._emit("on_room_list_update", changed)

    def _on_lobby_stats(self, params: Parameters) -> None:
        names = params.get(ParameterKey.LobbyName)
        types = params.get(ParameterKey.LobbyType)
        players = params.get(ParameterKey.PeerCount)
        rooms = params.get(ParameterKey.GameCount)
        if names is None or types is None or players is None or rooms is None:
            return
        self.lobby_statistics = [
            LobbyStatistics(TypedLobby(name, LobbyType(kind)), player_count, count)
            for name, kind, player_count, count in zip(
                to_python(names),
                to_python(types),
                to_python(players),
                to_python(rooms),
                strict=True,
            )
        ]
        self._emit("on_lobby_statistics_update", self.lobby_statistics)

    def _on_app_stats(self, params: Parameters) -> None:
        if (count := params.get(ParameterKey.MasterPeerCount)) is not None:
            self.players_on_master_count = int(count.value)
        if (count := params.get(ParameterKey.PeerCount)) is not None:
            self.players_in_rooms_count = int(count.value)
        if (count := params.get(ParameterKey.GameCount)) is not None:
            self.rooms_count = int(count.value)

    def _on_find_friends(self, response: OperationResponse) -> None:
        requested, self._friends_requested = self._friends_requested, []
        online = response.parameters.get(ParameterKey.FindFriendsRequestList)
        rooms = response.parameters.get(ParameterKey.FindFriendsResponseRoomIdList)
        friends: list[FriendInfo] = []
        if response.return_code == ErrorCode.Ok and online and rooms:
            friends = [
                FriendInfo(user_id, bool(is_online), str(room or ""))
                for user_id, is_online, room in zip(
                    requested, to_python(online), to_python(rooms), strict=False
                )
            ]
        self._emit("on_friend_list_update", friends)

    # -- entering rooms (Master Server -> Game Server) ---------------------------

    def _enter_room(self, enter: _EnterRoom) -> bool:
        return self._send_enter_room(
            enter, self._enter_room_params(enter, on_game_server=False)
        )

    def _send_enter_room(self, enter: _EnterRoom, params: Parameters) -> bool:
        if not self._can_matchmake() or not self.peer.send_operation(
            enter.operation, params
        ):
            return False
        self._enter = enter
        self.state = ClientState.Joining
        return True

    def _enter_room_params(
        self, enter: _EnterRoom, *, on_game_server: bool
    ) -> Parameters:
        options = enter.params
        params: Parameters = {}
        if options.room_name:
            params[ParameterKey.GameId] = StringParameter(options.room_name)
        if enter.join_mode != JoinMode.Default:
            params[ParameterKey.JoinMode] = Int8Parameter(enter.join_mode)
        if options.expected_users:
            params[ParameterKey.Add] = string_array(options.expected_users)
        if not on_game_server:
            params.update(_lobby_params(options.lobby or self.current_lobby))
            return params

        if enter.join_mode != JoinMode.RejoinOnly:
            params[ParameterKey.PlayerProperties] = self._local_player_properties(
                options
            )
            params[ParameterKey.Broadcast] = BooleanParameter(value=True)
        creates = enter.operation == OperationCode.CreateGame
        if creates or enter.join_mode == JoinMode.CreateIfNotExists:
            params.update(_room_option_params(options.room_options or RoomOptions()))
        return params

    def _local_player_properties(self, options: EnterRoomParams) -> ParameterBase[Any]:
        properties = {
            to_param(k): to_param(v) for k, v in options.player_properties.items()
        }
        if self.local_player.nick_name:
            properties[_byte_key(ActorPropertyKey.PlayerName)] = StringParameter(
                self.local_player.nick_name
            )
        return to_hashtable(properties)

    def _on_enter_room(self, response: OperationResponse) -> None:
        enter = self._enter
        if enter is None or self.state != ClientState.Joining:
            return
        if self.server == ServerType.GameServer:
            self._on_entered_game_server(enter, response)
            return
        if response.return_code != ErrorCode.Ok:
            self._enter = None
            self.state = self._master_state()
            self._emit_enter_failed(enter, response)
            return

        params = response.parameters
        # The Master Server hands out a token bound to the chosen Game Server.
        self._store_authentication(params)
        if (room_name := params.get(ParameterKey.GameId)) is not None:
            enter.params = dataclasses.replace(
                enter.params, room_name=str(room_name.value)
            )
        address = params.get(ParameterKey.Address)
        if address is None or not enter.params.room_name:
            self._fail(DisconnectCause.DisconnectByServerLogic)
            return
        if enter.operation == OperationCode.JoinRandomGame:
            enter.operation = OperationCode.JoinGame  # The room is known now.
        self.game_server_address = str(address.value)
        self.in_lobby = False
        self.state = ClientState.DisconnectingFromMasterServer
        self._next_hop = (ServerType.GameServer, self.game_server_address)
        self.peer.disconnect()

    def _send_enter_room_to_game_server(self) -> None:
        if self._enter is None:
            self._fail(DisconnectCause.OperationNotAllowedInCurrentState)
            return
        self.state = ClientState.Joining
        self.peer.send_operation(
            self._enter.operation,
            self._enter_room_params(self._enter, on_game_server=True),
        )

    def _on_entered_game_server(
        self, enter: _EnterRoom, response: OperationResponse
    ) -> None:
        self._enter = None
        if response.return_code != ErrorCode.Ok:
            self._emit_enter_failed(enter, response)
            self._leave_game_server()
            return

        params = response.parameters
        room = Room(enter.params.room_name or "", client=self)
        if (properties := params.get(ParameterKey.GameProperties)) is not None:
            room.update(to_python(properties))
        if (actor_nr := params.get(ParameterKey.ActorNr)) is not None:
            self.local_player.actor_number = int(actor_nr.value)
        self.local_player.is_inactive = False
        room.players[self.local_player.actor_number] = self.local_player
        if (actors := params.get(ParameterKey.PlayerProperties)) is not None:
            for number, properties in to_python(actors).items():
                player = self._player(room, int(number))
                if not player.is_local:
                    player.update(properties)
        if (actor_list := params.get(ParameterKey.ActorList)) is not None:
            for number in to_python(actor_list):
                self._player(room, int(number))

        self.current_room = room
        self._last_room = room.name
        self.state = ClientState.Joined
        if enter.operation == OperationCode.CreateGame or (
            enter.join_mode == JoinMode.CreateIfNotExists
            and self.local_player.actor_number == 1
        ):
            self._emit("on_created_room")
        self._emit("on_joined_room")

    def _player(self, room: Room, actor_number: int) -> Player:
        """Return the room's player ``actor_number``, adding it if unknown."""
        if (player := room.players.get(actor_number)) is None:
            player = room.players[actor_number] = Player(actor_number, client=self)
        return player

    # -- in-room events (Game Server) -------------------------------------------

    def _on_join_event(self, room: Room, params: Parameters) -> None:
        actor = params.get(ParameterKey.ActorNr)
        if actor is None:
            return
        number = int(actor.value)
        is_new = number not in room.players
        player = self._player(room, number)
        if (actor_list := params.get(ParameterKey.ActorList)) is not None:
            for other in to_python(actor_list):
                self._player(room, int(other))
        if player.is_local:
            return  # Our own join; the operation response already set us up.
        was_inactive = player.is_inactive
        player.is_inactive = False
        if (properties := params.get(ParameterKey.PlayerProperties)) is not None:
            player.update(to_python(properties))
        if is_new or was_inactive:
            self._emit("on_player_entered_room", player)

    def _on_leave_event(self, room: Room, params: Parameters) -> None:
        actor = params.get(ParameterKey.ActorNr)
        if actor is None:
            return
        number = int(actor.value)
        inactive = params.get(ParameterKey.IsInactive)
        if (player := room.players.get(number)) is not None:
            if inactive is not None and inactive.value:
                player.is_inactive = True
            else:
                del room.players[number]
            self._emit("on_player_left_room", player)

        new_master = params.get(ParameterKey.MasterClientId)
        if new_master is not None and int(new_master.value):
            self._switch_master_client(room, int(new_master.value))
        elif number == room.master_client_id:
            # Not announced by the server: the lowest active actor takes over.
            active = [n for n, p in room.players.items() if not p.is_inactive]
            if active:
                self._switch_master_client(room, min(active))

    def _on_properties_changed(self, room: Room, params: Parameters) -> None:
        properties = params.get(ParameterKey.Properties)
        if properties is None:
            return
        changed: dict[Any, Any] = to_python(properties)
        target = params.get(ParameterKey.TargetActorNr)
        if target is not None and int(target.value):
            player = self._player(room, int(target.value))
            player.update(changed)
            self._emit("on_player_properties_update", player, changed)
            return
        old_master = room.master_client_id
        room.update(changed)
        self._emit("on_room_properties_update", changed)
        if room.master_client_id != old_master:
            new_master, room.master_client_id = room.master_client_id, old_master
            self._switch_master_client(room, new_master)

    def _switch_master_client(self, room: Room, actor_number: int) -> None:
        if actor_number == room.master_client_id:
            return
        room.master_client_id = actor_number
        if (player := room.players.get(actor_number)) is not None:
            self._emit("on_master_client_switched", player)

    def _emit_enter_failed(
        self, enter: _EnterRoom, response: OperationResponse
    ) -> None:
        self._emit(
            enter.failed_callback, response.return_code, response.debug_message or ""
        )

    def _leave_game_server(self) -> None:
        """Drop the room and hop back to the Master Server."""
        was_in_room = self.current_room is not None
        self.current_room = None
        self.state = ClientState.DisconnectingFromGameServer
        if self.master_server_address is not None:
            self._next_hop = (ServerType.MasterServer, self.master_server_address)
        self.peer.disconnect()
        if was_in_room:
            self._emit("on_left_room")

    # -- failures -------------------------------------------------------------

    def _fail_operation(self, return_code: int) -> None:
        self._fail(
            _AUTH_FAILURE_CAUSES.get(
                return_code, DisconnectCause.DisconnectByServerLogic
            )
        )

    def _fail(self, cause: DisconnectCause) -> None:
        """Disconnect for ``cause``; ``on_disconnected`` fires once the peer is down."""
        self.disconnect_cause = cause
        self._next_hop = None
        if self.peer.state == PeerState.Disconnected:
            self._set_disconnected()
            return
        self.state = ClientState.Disconnecting
        self.peer.disconnect()

    def _set_disconnected(self) -> None:
        self._next_hop = None
        self._enter = None
        self.current_room = None
        self.in_lobby = False
        if self._pinger is not None:
            self._pinger.close()
            self._pinger = None
        self.state = ClientState.Disconnected
        self._emit("on_disconnected", self.disconnect_cause)


def _int_array(values: list[int]) -> SliceParameter[Int32Parameter]:
    return SliceParameter(
        [Int32Parameter(v) for v in values], element_type=ParameterType.Int32Type
    )


def _lobby_params(lobby: TypedLobby) -> Parameters:
    if lobby.is_default:
        return {}
    return {
        ParameterKey.LobbyName: StringParameter(lobby.name),
        ParameterKey.LobbyType: Int8Parameter(lobby.type),
    }


def _room_option_params(options: RoomOptions) -> Parameters:
    properties = {
        to_param(k): to_param(v) for k, v in options.custom_room_properties.items()
    }
    properties[_byte_key(GamePropertyKey.IsOpen)] = BooleanParameter(options.is_open)
    properties[_byte_key(GamePropertyKey.IsVisible)] = BooleanParameter(
        options.is_visible
    )
    properties[_byte_key(GamePropertyKey.CleanupCacheOnLeave)] = BooleanParameter(
        value=True
    )
    if options.max_players > 0:
        properties[_byte_key(GamePropertyKey.MaxPlayers)] = Int8Parameter(
            min(options.max_players, _BYTE_MAX)
        )
        if options.max_players > _BYTE_MAX:
            properties[_byte_key(GamePropertyKey.MaxPlayersInt)] = Int32Parameter(
                options.max_players
            )
    if options.custom_room_properties_for_lobby:
        properties[_byte_key(GamePropertyKey.PropsListedInLobby)] = string_array(
            options.custom_room_properties_for_lobby
        )

    params: Parameters = {
        ParameterKey.GameProperties: to_hashtable(properties),
        ParameterKey.CheckUserOnJoin: BooleanParameter(value=True),
        ParameterKey.CleanupCacheOnLeave: BooleanParameter(value=True),
    }
    if options.player_ttl:
        params[ParameterKey.PlayerTTL] = Int32Parameter(options.player_ttl)
    if options.empty_room_ttl:
        params[ParameterKey.EmptyRoomLiveTime] = Int32Parameter(options.empty_room_ttl)
    if options.suppress_room_events:
        params[ParameterKey.SuppressRoomEvents] = BooleanParameter(value=True)
    if options.publish_user_id:
        params[ParameterKey.PublishUserId] = BooleanParameter(value=True)
    if options.plugins is not None:
        params[ParameterKey.Plugins] = string_array(options.plugins)
    return params


def _auth_value_params(auth: AuthenticationValues) -> Parameters:
    params: Parameters = {}
    if auth.user_id:
        params[ParameterKey.UserId] = StringParameter(auth.user_id)
    if auth.auth_type != CustomAuthenticationType.NoAuth:
        params[ParameterKey.ClientAuthenticationType] = Int8Parameter(auth.auth_type)
        if auth.auth_get_parameters:
            params[ParameterKey.ClientAuthenticationParams] = StringParameter(
                auth.auth_get_parameters
            )
        if isinstance(auth.auth_post_data, str):
            params[ParameterKey.ClientAuthenticationData] = StringParameter(
                auth.auth_post_data
            )
        elif isinstance(auth.auth_post_data, bytes):
            params[ParameterKey.ClientAuthenticationData] = Int8SliceParameter(
                auth.auth_post_data
            )
    return params
