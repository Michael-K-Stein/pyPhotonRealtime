"""``RealtimeClient``: the high-level Photon Realtime workflow.

Owns one :class:`PhotonPeer` at a time and hops it across servers::

    Name Server   --Authenticate-->  master address
    Master Server --Join/Create--->  game server address + room
    Game Server   --Join/Create--->  in room: events, properties, players

Like the official SDK, it is driven entirely by :meth:`RealtimeClient.service`;
call it regularly from one thread. Callbacks fire from inside ``service``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyphotonrealtime.peer import PhotonPeer
from pyphotonrealtime.realtime.player import Player
from pyphotonrealtime.realtime.state import ClientState, DisconnectCause, ServerType

if TYPE_CHECKING:
    from pyphotonrealtime.peer import EventData, OperationResponse, StatusCode
    from pyphotonrealtime.realtime.callbacks import CallbackTarget
    from pyphotonrealtime.realtime.options import (
        EnterRoomParams,
        RaiseEventArgs,
        SendOptions,
    )
    from pyphotonrealtime.realtime.room import Room, RoomInfo
    from pyphotonrealtime.realtime.settings import AppSettings


class RealtimeClient:
    """Connects to Photon, matchmakes, and tracks the current room."""

    def __init__(self) -> None:
        """Create a client; nothing touches the network until ``connect_*``."""
        self.state = ClientState.PeerCreated
        self.server = ServerType.NameServer
        self.disconnect_cause = DisconnectCause.NoCause
        self.settings: AppSettings | None = None

        self.peer = PhotonPeer(listener=self)
        self.local_player = Player(actor_number=-1, is_local=True)
        self.current_room: Room | None = None
        self.room_list: dict[str, RoomInfo] = {}

        self.user_id: str | None = None
        self.cloud_region: str | None = None
        self.master_server_address: str | None = None
        self.game_server_address: str | None = None

        self._callback_targets: list[CallbackTarget] = []

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

    # -- connection -------------------------------------------------------

    def connect_using_settings(self, settings: AppSettings) -> bool:
        """Connect to the Name Server (or straight to a Master Server).

        Returns:
            Whether the connection attempt was started.
        """
        raise NotImplementedError

    def connect_to_region_master(self, region: str) -> bool:
        """Connect via the Name Server to the Master Server of ``region``.

        Returns:
            Whether the connection attempt was started.
        """
        raise NotImplementedError

    def reconnect_and_rejoin(self) -> bool:
        """Reconnect after an unexpected disconnect and rejoin the last room.

        Returns:
            Whether the attempt was started.
        """
        raise NotImplementedError

    def disconnect(self) -> None:
        """Leave any room and disconnect from Photon."""
        raise NotImplementedError

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
        raise NotImplementedError

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

    def on_operation_response(self, response: OperationResponse) -> None:
        """Handle Authenticate / Join / Create / SetProperties / GetRegions."""

    def on_event(self, event: EventData) -> None:
        """Update room state from built-in events, then forward to targets."""
        self._emit("on_event", event)
