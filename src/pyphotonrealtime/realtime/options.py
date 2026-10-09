"""Option bundles for matchmaking and events."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyphotonrealtime.realtime.lobby import TypedLobby


class ReceiverGroup(IntEnum):
    """Who receives a raised event."""

    Others = 0
    All = 1
    MasterClient = 2


class EventCaching(IntEnum):
    """How the server caches a raised event for late joiners."""

    DoNotCache = 0
    AddToRoomCache = 4
    AddToRoomCacheGlobal = 5
    RemoveFromRoomCache = 6
    RemoveFromRoomCacheForActorsLeft = 7


@dataclass(slots=True, kw_only=True)
class RoomOptions:
    """Settings for a room created by this client."""

    is_visible: bool = True
    is_open: bool = True
    max_players: int = 0
    player_ttl: int = 0
    empty_room_ttl: int = 0
    custom_room_properties: dict[Any, Any] = field(default_factory=dict)
    custom_room_properties_for_lobby: list[str] = field(default_factory=list)
    publish_user_id: bool = False
    suppress_room_events: bool = False
    plugins: list[str] | None = None


@dataclass(slots=True, kw_only=True)
class EnterRoomParams:
    """Arguments for creating, joining or rejoining a room."""

    room_name: str | None = None
    """None on create lets the server pick a unique name."""
    room_options: RoomOptions | None = None
    lobby: TypedLobby | None = None
    """Lobby to list a new room in; None uses the client's current lobby."""
    player_properties: dict[Any, Any] = field(default_factory=dict)
    expected_users: list[str] | None = None
    """User ids to reserve slots for in the room."""


@dataclass(slots=True, kw_only=True)
class RaiseEventArgs:
    """Routing and caching for ``op_raise_event``."""

    receivers: ReceiverGroup = ReceiverGroup.Others
    target_actors: list[int] | None = None
    interest_group: int = 0
    caching: EventCaching = EventCaching.DoNotCache


@dataclass(slots=True, kw_only=True)
class SendOptions:
    """Delivery options for an operation."""

    reliable: bool = True
    encrypt: bool = False
    channel: int = 0
