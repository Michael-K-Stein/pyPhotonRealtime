from typing import Any, Dict, Optional

from pyphotonrealtime.realtime.player import Player


class RoomInfo:
    """A room as listed in a lobby (read-only snapshot)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.player_count = 0
        self.max_players = 0
        self.is_open = True
        self.is_visible = True
        self.removed_from_list = False
        self.custom_properties: Dict[Any, Any] = {}

    def __repr__(self) -> str:
        return f"RoomInfo({self.name!r}, {self.player_count}/{self.max_players})"


class Room(RoomInfo):
    """The room the client is currently in."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.players: Dict[int, Player] = {}
        self.master_client_id = 0

    @property
    def master_client(self) -> Optional[Player]:
        return self.players.get(self.master_client_id)

    def set_custom_properties(self, properties: Dict[Any, Any]) -> bool:
        """TODO: OpSetProperties on the room (with expected-properties CAS)."""
        raise NotImplementedError
