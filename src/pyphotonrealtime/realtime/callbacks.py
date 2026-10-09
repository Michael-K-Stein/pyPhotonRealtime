"""Callback interfaces, matching the C# SDK's ``I*Callbacks``.

Subclass any of these, override what you need, and register the instance with
:meth:`RealtimeClient.add_callback_target`. All methods default to no-ops.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyphotonrealtime.peer import EventData
    from pyphotonrealtime.realtime.lobby import FriendInfo, LobbyStatistics
    from pyphotonrealtime.realtime.player import Player
    from pyphotonrealtime.realtime.room import RoomInfo
    from pyphotonrealtime.realtime.state import DisconnectCause


class ConnectionCallbacks:
    """Connection lifecycle (``IConnectionCallbacks``)."""

    def on_connected(self) -> None:
        """Connected to any server; usually wait for ``on_connected_to_master``."""

    def on_connected_to_master(self) -> None:
        """Authenticated on the Master Server; ready for matchmaking."""

    def on_disconnected(self, cause: DisconnectCause) -> None:
        """Disconnected from Photon, for the given reason."""

    def on_region_list_received(self, regions: dict[str, str]) -> None:
        """The Name Server sent its regions (code -> master address)."""

    def on_custom_authentication_response(self, data: dict[str, Any]) -> None:
        """A custom authentication provider returned extra data."""

    def on_custom_authentication_failed(self, debug_message: str) -> None:
        """Custom authentication rejected the client."""


class MatchmakingCallbacks:
    """Room creation and joining (``IMatchmakingCallbacks``)."""

    def on_friend_list_update(self, friends: list[FriendInfo]) -> None:
        """The answer to ``op_find_friends`` arrived."""

    def on_created_room(self) -> None:
        """This client created the room it is joining."""

    def on_create_room_failed(self, return_code: int, message: str) -> None:
        """Creating a room failed (e.g. the name is taken)."""

    def on_joined_room(self) -> None:
        """Entered a room (created or joined); ``current_room`` is set."""

    def on_join_room_failed(self, return_code: int, message: str) -> None:
        """Joining a specific room failed."""

    def on_join_random_failed(self, return_code: int, message: str) -> None:
        """No room matched the random-join filter."""

    def on_left_room(self) -> None:
        """Left the room; on_connected_to_master follows once back on the Master."""


class LobbyCallbacks:
    """Lobby membership and room lists (``ILobbyCallbacks``)."""

    def on_joined_lobby(self) -> None:
        """Entered a lobby; room list updates will follow."""

    def on_left_lobby(self) -> None:
        """Left the lobby."""

    def on_room_list_update(self, rooms: list[RoomInfo]) -> None:
        """Rooms were added, changed or removed in the current lobby."""

    def on_lobby_statistics_update(self, stats: list[LobbyStatistics]) -> None:
        """Lobby statistics changed (requires ``enable_lobby_statistics``)."""


class InRoomCallbacks:
    """Changes inside the current room (``IInRoomCallbacks``)."""

    def on_player_entered_room(self, player: Player) -> None:
        """A remote player joined the room."""

    def on_player_left_room(self, player: Player) -> None:
        """A remote player left (or became inactive)."""

    def on_room_properties_update(self, changed: dict[Any, Any]) -> None:
        """Room custom properties changed."""

    def on_player_properties_update(
        self, player: Player, changed: dict[Any, Any]
    ) -> None:
        """A player's custom properties changed."""

    def on_master_client_switched(self, new_master: Player) -> None:
        """The master client changed."""


class OnEventCallback:
    """Raw access to every event (``IOnEventCallback``)."""

    def on_event(self, event: EventData) -> None:
        """Any event arrived, including custom events from ``op_raise_event``."""


type CallbackTarget = (
    ConnectionCallbacks
    | MatchmakingCallbacks
    | LobbyCallbacks
    | InRoomCallbacks
    | OnEventCallback
)
