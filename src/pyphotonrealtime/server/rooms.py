"""Rooms, their players and properties, as kept by the self-hosted server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.hashtable_param import HashtableParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.nil_param import NilParameter
from pyphotonrealtime.protocol.param.parameter_type import ParameterType
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.protocol.property_keys import ActorPropertyKey, GamePropertyKey
from pyphotonrealtime.realtime._convert import to_python
from pyphotonrealtime.realtime.lobby import TypedLobby

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from pyphotonrealtime.peer import Parameters
    from pyphotonrealtime.protocol.param.base import ParameterBase
    from pyphotonrealtime.protocol.param.dictionary_param_base import (
        DictionaryParameterBase,
    )
    from pyphotonrealtime.server.connection import Connection

    type Param = ParameterBase[Any]

MAX_PLAYERS = GamePropertyKey.MaxPlayers.value.value
IS_VISIBLE = GamePropertyKey.IsVisible.value.value
IS_OPEN = GamePropertyKey.IsOpen.value.value
PLAYER_COUNT = GamePropertyKey.PlayerCount.value.value
REMOVED = GamePropertyKey.Removed.value.value
PROPS_LISTED_IN_LOBBY = GamePropertyKey.PropsListedInLobby.value.value
MASTER_CLIENT_ID = GamePropertyKey.MasterClientId.value.value
EXPECTED_USERS = GamePropertyKey.ExpectedUsers.value.value
MAX_PLAYERS_INT = GamePropertyKey.MaxPlayersInt.value.value
IS_INACTIVE = ActorPropertyKey.IsInactive.value.value
USER_ID = ActorPropertyKey.UserId.value.value

# Shown in lobby listings next to the custom properties listed there.
_LISTED_WELL_KNOWN = (MAX_PLAYERS, MAX_PLAYERS_INT, IS_OPEN)
_BYTE_MAX = 0xFF


class Properties:
    """A property table, keyed by plain value but keeping the wire types.

    Clients look keys up by value, so ``Int8Parameter(255)`` and a plain
    ``255`` are the same key here; values go back out as they came in.
    """

    def __init__(self) -> None:
        """Create an empty table."""
        self._items: dict[Any, tuple[Param, Param]] = {}

    def update(self, table: DictionaryParameterBase[Any, Any]) -> None:
        """Merge ``table`` in; a null value deletes the key."""
        for key, value in table.items():
            plain = to_python(key)
            if isinstance(value, NilParameter):
                self._items.pop(plain, None)
            else:
                self._items[plain] = (key, value)

    def get(self, key: object, default: Any = None) -> Any:  # noqa: ANN401
        """The plain value of ``key``, or ``default``.

        Returns:
            The value.
        """
        item = self._items.get(key)
        return default if item is None else to_python(item[1])

    def matches(self, expected: DictionaryParameterBase[Any, Any]) -> bool:
        """Whether every expected value (null meaning unset) is current.

        Returns:
            True if all match.
        """
        return all(
            self.get(to_python(key)) == to_python(value)
            for key, value in expected.items()
        )

    def to_hashtable(
        self, keys: Iterable[Any] | None = None
    ) -> HashtableParameter[Any, Any]:
        """The table (or just ``keys`` of it) as a Hashtable parameter.

        Returns:
            The hashtable.
        """
        items = (
            self._items.values()
            if keys is None
            else [self._items[k] for k in keys if k in self._items]
        )
        return HashtableParameter(dict(items))


@dataclass(slots=True, eq=False)
class Actor:
    """A player in a room; ``connection`` is None while inactive."""

    number: int
    user_id: str
    connection: Connection | None
    properties: Properties = field(default_factory=Properties)
    groups: set[int] = field(default_factory=lambda: {0})
    """Interest groups this actor receives; group 0 is everyone's."""
    all_groups: bool = False
    """Whether the actor subscribed to every interest group."""
    inactive_since: float | None = None
    """When the actor left with a player TTL; None while active."""

    @property
    def is_active(self) -> bool:
        """Whether the actor is connected (not left with a player TTL)."""
        return self.connection is not None

    def receives_group(self, group: int) -> bool:
        """Whether events sent to interest ``group`` reach this actor.

        Returns:
            True if subscribed.
        """
        return group == 0 or self.all_groups or group in self.groups

    def properties_for(self, room: Room) -> HashtableParameter[Any, Any]:
        """Properties as other players see them: user id only if published.

        Returns:
            The hashtable.
        """
        table = self.properties.to_hashtable()
        if room.publish_user_id:
            table.value[Int8Parameter(USER_ID)] = StringParameter(self.user_id)
        if not self.is_active:
            table.value[Int8Parameter(IS_INACTIVE)] = BooleanParameter(value=True)
        return table


@dataclass(slots=True)
class CachedEvent:
    """A raised event kept for players who join later."""

    sender: int
    """0 for events cached globally (they outlive their sender)."""
    code: int
    params: Parameters


@dataclass(slots=True, eq=False)
class Room:
    """A room on the Game Server."""

    name: str
    lobby: TypedLobby = field(default_factory=TypedLobby)
    properties: Properties = field(default_factory=Properties)
    actors: dict[int, Actor] = field(default_factory=dict)
    cache: list[CachedEvent] = field(default_factory=list)
    master_client_id: int = 0
    player_ttl: int = 0
    empty_room_ttl: int = 0
    publish_user_id: bool = False
    suppress_room_events: bool = False
    cleanup_cache_on_leave: bool = True
    last_actor_number: int = 0
    empty_since: float | None = None
    """When the last active player left; None while anyone is connected."""

    @property
    def max_players(self) -> int:
        """Player limit; 0 means none."""
        return int(
            self.properties.get(MAX_PLAYERS_INT) or self.properties.get(MAX_PLAYERS, 0)
        )

    @property
    def is_open(self) -> bool:
        """Whether players may join."""
        return bool(self.properties.get(IS_OPEN, True))

    @property
    def is_visible(self) -> bool:
        """Whether the room is listed in its lobby and found by random joins."""
        return bool(self.properties.get(IS_VISIBLE, True))

    @property
    def expected_users(self) -> list[str]:
        """User ids that have a slot reserved."""
        return [str(u) for u in self.properties.get(EXPECTED_USERS) or []]

    @property
    def player_count(self) -> int:
        """Active plus inactive players, as Photon counts them."""
        return len(self.actors)

    def active_actors(self) -> Iterator[Actor]:
        """Players currently connected.

        Yields:
            Each active actor.
        """
        yield from (a for a in self.actors.values() if a.is_active)

    def find_user(self, user_id: str) -> Actor | None:
        """The actor of ``user_id``, active or not.

        Returns:
            The actor, or None.
        """
        return next((a for a in self.actors.values() if a.user_id == user_id), None)

    def is_full_for(self, user_id: str) -> bool:
        """Whether ``user_id`` can't get a slot (reserved slots count as taken).

        Returns:
            True if full.
        """
        if not self.max_players:
            return False
        if user_id in self.expected_users:
            return False
        reserved = sum(1 for u in self.expected_users if self.find_user(u) is None)
        return self.player_count + reserved >= self.max_players

    def add_actor(self, user_id: str, connection: Connection) -> Actor:
        """Seat a new player; the first one becomes master client.

        Returns:
            The actor.
        """
        self.last_actor_number += 1
        actor = Actor(self.last_actor_number, user_id, connection)
        self.actors[actor.number] = actor
        if not self.master_client_id:
            self.master_client_id = actor.number
        self.empty_since = None
        return actor

    def game_properties(self) -> HashtableParameter[Any, Any]:
        """Every room property, including the server-managed ones.

        Returns:
            The hashtable.
        """
        table = self.properties.to_hashtable()
        table.value[Int8Parameter(MASTER_CLIENT_ID)] = Int32Parameter(
            self.master_client_id
        )
        table.value[Int8Parameter(PLAYER_COUNT)] = Int8Parameter(
            min(self.player_count, _BYTE_MAX)
        )
        return table

    def actor_list(self) -> SliceParameter[Int32Parameter]:
        """Actor numbers of everyone in the room.

        Returns:
            An ``int[]`` parameter.
        """
        return SliceParameter(
            [Int32Parameter(n) for n in self.actors],
            element_type=ParameterType.Int32Type,
        )

    def lobby_listing(self) -> HashtableParameter[Any, Any]:
        """What a lobby shows of this room.

        Returns:
            The hashtable.
        """
        listed = self.properties.get(PROPS_LISTED_IN_LOBBY) or []
        table = self.properties.to_hashtable([*_LISTED_WELL_KNOWN, *listed])
        table.value[Int8Parameter(PLAYER_COUNT)] = Int8Parameter(
            min(self.player_count, _BYTE_MAX)
        )
        table.value[Int8Parameter(IS_OPEN)] = BooleanParameter(value=self.is_open)
        return table

    @property
    def listed(self) -> bool:
        """Whether lobbies show this room."""
        return self.is_visible and bool(self.actors)
