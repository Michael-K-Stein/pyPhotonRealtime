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
from typing import TYPE_CHECKING, Any

from pyphotonrealtime.peer import PeerState, PhotonPeer, StatusCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.realtime._convert import to_python
from pyphotonrealtime.realtime.authentication import (
    AuthenticationValues,
    AuthMode,
    CustomAuthenticationType,
)
from pyphotonrealtime.realtime.error_code import ErrorCode
from pyphotonrealtime.realtime.player import Player
from pyphotonrealtime.realtime.region import Region, RegionPinger
from pyphotonrealtime.realtime.settings import DEFAULT_MASTER_SERVER_PORT_TCP
from pyphotonrealtime.realtime.state import ClientState, DisconnectCause, ServerType

if TYPE_CHECKING:
    from pyphotonrealtime.peer import EventData, OperationResponse, Parameters
    from pyphotonrealtime.protocol.param.base import ParameterBase
    from pyphotonrealtime.realtime.callbacks import CallbackTarget
    from pyphotonrealtime.realtime.options import (
        EnterRoomParams,
        RaiseEventArgs,
        SendOptions,
    )
    from pyphotonrealtime.realtime.room import Room, RoomInfo
    from pyphotonrealtime.realtime.settings import AppSettings
    from pyphotonrealtime.transport import Transport

# ConnectionProtocol.Tcp and EncryptionMode.PayloadEncryption, sent with AuthOnce.
_PROTOCOL_TCP = 1
_PAYLOAD_ENCRYPTION = 0
# Keys of the EncryptionData dictionary in the AuthOnce response.
_ENCRYPTION_SECRET1 = 1

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
        self.local_player = Player(actor_number=-1, is_local=True)
        self.current_room: Room | None = None
        self.room_list: dict[str, RoomInfo] = {}

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
        """Reconnect after an unexpected disconnect and rejoin the last room.

        Returns:
            Whether the attempt was started.
        """
        raise NotImplementedError  # Needs the Game Server hop (M3).

    def disconnect(self) -> None:
        """Leave any room and disconnect from Photon."""
        if self.state in {
            ClientState.PeerCreated,
            ClientState.Disconnecting,
            ClientState.Disconnected,
        }:
            return
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

    def op_join_lobby(self, lobby: str | None = None) -> bool:
        """Join ``lobby`` (the default lobby if None) to receive room lists.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_leave_lobby(self) -> bool:
        """Leave the current lobby.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_create_room(self, params: EnterRoomParams) -> bool:
        """Create a room and join it.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_join_room(self, params: EnterRoomParams) -> bool:
        """Join an existing room by name.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_join_or_create_room(self, params: EnterRoomParams) -> bool:
        """Join a room by name, creating it if it doesn't exist.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_join_random_room(
        self,
        expected_properties: dict[Any, Any] | None = None,
        expected_max_players: int = 0,
    ) -> bool:
        """Join any open room matching the filter.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_rejoin_room(self, room_name: str) -> bool:
        """Return to a room this client was inactive in (``player_ttl``).

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_find_friends(self, user_ids: list[str]) -> bool:
        """Ask which of ``user_ids`` are online and in which rooms.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

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

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_raise_event(
        self,
        event_code: int,
        content: object,
        args: RaiseEventArgs | None = None,
        send_options: SendOptions | None = None,
    ) -> bool:
        """Send a custom event (codes 1-199) to other players in the room.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_change_groups(self, remove: list[int] | None, add: list[int] | None) -> bool:
        """Change which interest groups this client receives events from.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_set_properties_of_room(
        self,
        properties: dict[Any, Any],
        expected: dict[Any, Any] | None = None,
    ) -> bool:
        """Set room properties, optionally only if ``expected`` still holds.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_set_properties_of_actor(
        self, actor_number: int, properties: dict[Any, Any]
    ) -> bool:
        """Set a player's custom properties.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def op_custom(
        self,
        operation_code: int,
        parameters: dict[int, Any],
        send_options: SendOptions | None = None,
    ) -> bool:
        """Send any operation; the escape hatch for game-specific ops.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

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
            case _:
                pass

    def on_event(self, event: EventData) -> None:
        """Update room state from built-in events, then forward to targets."""
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
        if self.server != ServerType.NameServer:
            self.state = ClientState.ConnectedToMasterServer
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
        if self._pinger is not None:
            self._pinger.close()
            self._pinger = None
        self.state = ClientState.Disconnected
        self._emit("on_disconnected", self.disconnect_cause)


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
