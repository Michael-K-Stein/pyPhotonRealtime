from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional


class ReceiverGroup(IntEnum):
    Others = 0
    All = 1
    MasterClient = 2


class EventCaching(IntEnum):
    DoNotCache = 0
    AddToRoomCache = 4
    AddToRoomCacheGlobal = 5
    RemoveFromRoomCache = 6
    RemoveFromRoomCacheForActorsLeft = 7


@dataclass
class RoomOptions:
    is_visible: bool = True
    is_open: bool = True
    max_players: int = 0
    player_ttl: int = 0
    empty_room_ttl: int = 0
    custom_room_properties: Dict[Any, Any] = field(default_factory=dict)
    custom_room_properties_for_lobby: List[str] = field(default_factory=list)
    publish_user_id: bool = False
    suppress_room_events: bool = False
    plugins: Optional[List[str]] = None


@dataclass
class EnterRoomParams:
    room_name: Optional[str] = None
    room_options: Optional[RoomOptions] = None
    lobby: Optional[str] = None
    player_properties: Dict[Any, Any] = field(default_factory=dict)
    expected_users: Optional[List[str]] = None
    rejoin: bool = False


@dataclass
class RaiseEventArgs:
    receivers: ReceiverGroup = ReceiverGroup.Others
    target_actors: Optional[List[int]] = None
    interest_group: int = 0
    caching: EventCaching = EventCaching.DoNotCache


@dataclass
class SendOptions:
    reliable: bool = True
    encrypt: bool = False
    channel: int = 0
