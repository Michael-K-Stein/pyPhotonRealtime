"""Rooms: lobby listings and the room the client is in."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyphotonrealtime.protocol.property_keys import GamePropertyKey

if TYPE_CHECKING:
    from pyphotonrealtime.realtime.player import Player


def _key(member: GamePropertyKey) -> Any:  # noqa: ANN401
    return member.value.value


_MAX_PLAYERS = _key(GamePropertyKey.MaxPlayers)
_MAX_PLAYERS_INT = _key(GamePropertyKey.MaxPlayersInt)
_IS_VISIBLE = _key(GamePropertyKey.IsVisible)
_IS_OPEN = _key(GamePropertyKey.IsOpen)
_PLAYER_COUNT = _key(GamePropertyKey.PlayerCount)
_REMOVED = _key(GamePropertyKey.Removed)
_MASTER_CLIENT_ID = _key(GamePropertyKey.MasterClientId)
_EXPECTED_USERS = _key(GamePropertyKey.ExpectedUsers)
_PLAYER_TTL = _key(GamePropertyKey.PlayerTtl)
_EMPTY_ROOM_TTL = _key(GamePropertyKey.EmptyRoomTtl)
_PROPS_LISTED_IN_LOBBY = _key(GamePropertyKey.PropsListedInLobby)


class RoomInfo:
    """A room as listed in a lobby (read-only snapshot)."""

    def __init__(self, name: str) -> None:
        """Create an empty listing for ``name``."""
        self.name = name
        self.player_count = 0
        self.max_players = 0
        self.is_open = True
        self.is_visible = True
        self.removed_from_list = False
        self.custom_properties: dict[Any, Any] = {}

    def update(self, properties: dict[Any, Any]) -> None:
        """Apply a (partial) property set as sent by the server.

        Well-known properties have byte keys and update the attributes; string
        keys are custom properties, where a ``None`` value deletes the key.
        """
        for key, value in properties.items():
            if isinstance(key, str):
                if value is None:
                    self.custom_properties.pop(key, None)
                else:
                    self.custom_properties[key] = value
            else:
                self._update_well_known(key, value)
        # Sent next to the byte-sized MaxPlayers when the limit exceeds 255.
        if (max_players := properties.get(_MAX_PLAYERS_INT)) is not None:
            self.max_players = int(max_players)

    def _update_well_known(self, key: object, value: Any) -> None:  # noqa: ANN401
        match key:
            case k if k == _MAX_PLAYERS:
                self.max_players = int(value)
            case k if k == _IS_VISIBLE:
                self.is_visible = bool(value)
            case k if k == _IS_OPEN:
                self.is_open = bool(value)
            case k if k == _PLAYER_COUNT:
                self.player_count = int(value)
            case k if k == _REMOVED:
                self.removed_from_list = bool(value)
            case _:
                pass

    def __repr__(self) -> str:
        """Short debug form: name and occupancy."""
        return f"RoomInfo({self.name!r}, {self.player_count}/{self.max_players})"


class Room(RoomInfo):
    """The room the client is currently in."""

    def __init__(self, name: str) -> None:
        """Create an empty room named ``name``."""
        super().__init__(name)
        self.players: dict[int, Player] = {}
        self.master_client_id = 0
        self.expected_users: list[str] = []
        self.player_ttl = 0
        self.empty_room_ttl = 0
        self.props_listed_in_lobby: list[str] = []

    @property
    def master_client(self) -> Player | None:
        """The current master client, if known."""
        return self.players.get(self.master_client_id)

    def _update_well_known(self, key: object, value: Any) -> None:  # noqa: ANN401
        match key:
            case k if k == _MASTER_CLIENT_ID:
                self.master_client_id = int(value)
            case k if k == _EXPECTED_USERS:
                self.expected_users = list(value or [])
            case k if k == _PLAYER_TTL:
                self.player_ttl = int(value)
            case k if k == _EMPTY_ROOM_TTL:
                self.empty_room_ttl = int(value)
            case k if k == _PROPS_LISTED_IN_LOBBY:
                self.props_listed_in_lobby = list(value or [])
            case _:
                super()._update_well_known(key, value)

    def set_custom_properties(self, properties: dict[Any, Any]) -> bool:
        """Merge ``properties`` into the room's properties on the server.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError
