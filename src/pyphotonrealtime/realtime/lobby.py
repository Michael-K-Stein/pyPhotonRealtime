"""Lobbies, lobby statistics and friend lookups."""

from dataclasses import dataclass
from enum import IntEnum


class LobbyType(IntEnum):
    """How a lobby lists rooms and matches ``op_join_random_room``."""

    Default = 0
    SqlLobby = 2
    """Random joins filter with SQL on room properties ``C0``-``C9``."""
    AsyncRandomLobby = 3


class MatchmakingMode(IntEnum):
    """Which room ``op_join_random_room`` prefers."""

    FillRoom = 0
    SerialMatching = 1
    RandomMatching = 2


class JoinMode(IntEnum):
    """``JoinMode`` parameter of the Join operation."""

    Default = 0
    CreateIfNotExists = 1
    JoinOrRejoin = 2
    RejoinOnly = 3


@dataclass(frozen=True, slots=True)
class TypedLobby:
    """A lobby name plus its type; the empty name is the default lobby."""

    name: str = ""
    type: LobbyType = LobbyType.Default

    @property
    def is_default(self) -> bool:
        """Whether this is the default lobby (which needs no parameters)."""
        return not self.name and self.type == LobbyType.Default


@dataclass(frozen=True, slots=True)
class LobbyStatistics:
    """Player and room counts of one lobby."""

    lobby: TypedLobby
    player_count: int
    room_count: int


@dataclass(frozen=True, slots=True)
class FriendInfo:
    """One answer to ``op_find_friends``; ``room`` is empty when not in a room."""

    user_id: str
    is_online: bool
    room: str = ""

    @property
    def is_in_room(self) -> bool:
        """Whether the friend is online and inside a room."""
        return self.is_online and bool(self.room)
