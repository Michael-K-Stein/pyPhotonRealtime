"""Rooms: lobby listings and the room the client is in."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyphotonrealtime.realtime.player import Player


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

    @property
    def master_client(self) -> Player | None:
        """The current master client, if known."""
        return self.players.get(self.master_client_id)

    def set_custom_properties(self, properties: dict[Any, Any]) -> bool:
        """Merge ``properties`` into the room's properties on the server.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError
