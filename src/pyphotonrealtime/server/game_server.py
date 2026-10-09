"""Game Server: rooms, players, events and properties."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, override

from pyphotonrealtime.protocol.event_code import EventCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.dictionary_param_base import (
    DictionaryParameterBase,
)
from pyphotonrealtime.protocol.param.hashtable_param import HashtableParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.realtime._convert import string_array, to_python
from pyphotonrealtime.realtime.error_code import ErrorCode
from pyphotonrealtime.realtime.lobby import JoinMode
from pyphotonrealtime.realtime.options import EventCaching, ReceiverGroup
from pyphotonrealtime.server.handler import RoleHandler
from pyphotonrealtime.server.master_server import lobby_of
from pyphotonrealtime.server.rooms import (
    EXPECTED_USERS,
    MASTER_CLIENT_ID,
    PLAYER_COUNT,
    CachedEvent,
    Room,
)

if TYPE_CHECKING:
    from pyphotonrealtime.peer import Parameters
    from pyphotonrealtime.server.connection import Connection
    from pyphotonrealtime.server.rooms import Actor
    from pyphotonrealtime.server.server import PhotonServer

# Photon return codes the shared ErrorCode enum doesn't list.
JOIN_FAILED_WITH_REJOINER_NOT_FOUND = 0x7FFF - 19  # 32748
CAS_FAILED = ErrorCode.InvalidOperation
# Player and empty-room TTLs are in ms; -1 keeps the player / room forever.
_FOREVER = -1
# Properties the server owns: clients can't set them directly.
_READ_ONLY = {PLAYER_COUNT}
_ENTER = {OperationCode.CreateGame, OperationCode.JoinGame}


class GameServer(RoleHandler):
    """Hosts the rooms and relays everything said in them."""

    def __init__(self, server: PhotonServer) -> None:
        """Serve on behalf of ``server``."""
        super().__init__(server)
        self.operations = {
            OperationCode.Authenticate: self._authenticate,
            OperationCode.CreateGame: self._enter,
            OperationCode.JoinGame: self._enter,
            OperationCode.RaiseEvent: self._raise_event,
            OperationCode.SetProperties: self._set_properties,
            OperationCode.GetProperties: self._get_properties,
            OperationCode.ChangeGroups: self._change_groups,
            OperationCode.Leave: self._leave_room,
        }

    @override
    def refuse(self, connection: Connection, operation: int) -> tuple[int, str] | None:
        if operation == OperationCode.Authenticate:
            return None
        if not connection.authenticated:
            return ErrorCode.OperationNotAllowedInCurrentState, "authenticate first"
        if operation not in _ENTER and connection.actor is None:
            return ErrorCode.OperationNotAllowedInCurrentState, "not in a room"
        return None

    def _authenticate(
        self,
        connection: Connection,
        operation: int,  # noqa: ARG002 -- one signature for all operations
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        if self.authenticate_with_token(connection, params, encrypted=encrypted):
            self.authenticated(connection, encrypted=encrypted)

    def _leave_room(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        inactive = params.get(ParameterKey.IsInactive)
        self._leave(connection, inactive=bool(inactive and inactive.value))
        connection.respond(operation, encrypt=encrypted)

    @override
    def disconnected(self, connection: Connection) -> None:
        self._leave(connection, inactive=True)

    @override
    def tick(self, now: float) -> None:
        for room in list(self.server.rooms.values()):
            expired = [
                actor
                for actor in room.actors.values()
                if actor.inactive_since is not None
                and room.player_ttl != _FOREVER
                and (now - actor.inactive_since) * 1000 >= room.player_ttl
            ]
            for actor in expired:
                self._remove_actor(room, actor)
            if expired:
                self.server.room_changed(room)
            if (
                not room.actors
                and room.empty_since is not None
                and room.empty_room_ttl != _FOREVER
                and (now - room.empty_since) * 1000 >= room.empty_room_ttl
            ):
                self.server.remove_room(room)

    # -- entering ------------------------------------------------------------------

    def _enter(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        assert connection.session is not None  # noqa: S101 -- authenticated
        user_id = connection.session.user_id
        name = params.get(ParameterKey.GameId)
        if name is None:
            connection.respond(
                operation,
                return_code=ErrorCode.InvalidOperation,
                message="missing room name",
                encrypt=encrypted,
            )
            return
        mode = params.get(ParameterKey.JoinMode)
        join_mode = JoinMode(int(mode.value)) if mode is not None else JoinMode.Default
        room = self.server.rooms.get(str(name.value))
        creates = room is None and (
            operation == OperationCode.CreateGame
            or join_mode == JoinMode.CreateIfNotExists
        )
        rejoiner = room.find_user(user_id) if room is not None else None
        error = self._enter_error(room, operation, join_mode, user_id, rejoiner)
        if error is not None:
            connection.respond(
                operation, return_code=error[0], message=error[1], encrypt=encrypted
            )
            return

        if creates:
            room = self._create_room(str(name.value), params)
        assert room is not None  # noqa: S101 -- created or found above
        if rejoiner is not None and not rejoiner.is_active:
            actor = rejoiner
            actor.connection = connection
            actor.inactive_since = None
            room.empty_since = None
        else:
            actor = room.add_actor(user_id, connection)
        if (properties := params.get(ParameterKey.PlayerProperties)) is not None and (
            isinstance(properties, DictionaryParameterBase)
        ):
            actor.properties.update(properties)
        connection.actor = actor

        connection.respond(
            operation,
            {
                ParameterKey.ActorNr: Int32Parameter(actor.number),
                ParameterKey.GameProperties: room.game_properties(),
                ParameterKey.PlayerProperties: HashtableParameter(
                    {
                        Int32Parameter(other.number): other.properties_for(room)
                        for other in room.actors.values()
                        if other is not actor
                    }
                ),
                ParameterKey.ActorList: room.actor_list(),
            },
            encrypt=encrypted,
        )
        if not room.suppress_room_events:
            join: Parameters = {
                ParameterKey.ActorNr: Int32Parameter(actor.number),
                ParameterKey.ActorList: room.actor_list(),
                ParameterKey.PlayerProperties: actor.properties_for(room),
            }
            for other in room.active_actors():
                assert other.connection is not None  # noqa: S101 -- active
                other.connection.send_event(EventCode.Join, join)
        for cached in room.cache:
            connection.send_event(
                cached.code,
                {**cached.params, ParameterKey.ActorNr: Int32Parameter(cached.sender)},
            )
        self.server.room_changed(room)

    @staticmethod
    def _enter_error(  # noqa: PLR0911 -- one per return code
        room: Room | None,
        operation: int,
        join_mode: JoinMode,
        user_id: str,
        rejoiner: Actor | None,
    ) -> tuple[int, str] | None:
        if room is None:
            if operation == OperationCode.CreateGame or (
                join_mode == JoinMode.CreateIfNotExists
            ):
                return None
            if join_mode == JoinMode.RejoinOnly:
                return JOIN_FAILED_WITH_REJOINER_NOT_FOUND, "nobody to rejoin as"
            return ErrorCode.GameDoesNotExist, "room does not exist"
        if operation == OperationCode.CreateGame:
            return ErrorCode.GameIdAlreadyExists, f"room {room.name!r} already exists"
        if rejoiner is not None and not rejoiner.is_active:
            return None
        if join_mode == JoinMode.RejoinOnly:
            return JOIN_FAILED_WITH_REJOINER_NOT_FOUND, "nobody to rejoin as"
        if not room.is_open:
            return ErrorCode.GameClosed, "room is closed"
        if room.is_full_for(user_id):
            return ErrorCode.GameFull, "room is full"
        return None

    def _create_room(self, name: str, params: Parameters) -> Room:
        room = Room(name, lobby=lobby_of(params))
        if (properties := params.get(ParameterKey.GameProperties)) is not None and (
            isinstance(properties, DictionaryParameterBase)
        ):
            room.properties.update(properties)
        if (ttl := params.get(ParameterKey.PlayerTTL)) is not None:
            room.player_ttl = int(ttl.value)
        if (ttl := params.get(ParameterKey.EmptyRoomLiveTime)) is not None:
            room.empty_room_ttl = int(ttl.value)
        if (flag := params.get(ParameterKey.CleanupCacheOnLeave)) is not None:
            room.cleanup_cache_on_leave = bool(flag.value)
        if (flag := params.get(ParameterKey.SuppressRoomEvents)) is not None:
            room.suppress_room_events = bool(flag.value)
        if (flag := params.get(ParameterKey.PublishUserId)) is not None:
            room.publish_user_id = bool(flag.value)
        if (users := params.get(ParameterKey.Add)) is not None:
            room.properties.update(
                HashtableParameter(
                    {Int8Parameter(EXPECTED_USERS): string_array(to_python(users))}
                )
            )
        self.server.rooms[name] = room
        return room

    # -- leaving -------------------------------------------------------------------

    def _leave(self, connection: Connection, *, inactive: bool) -> None:
        actor, connection.actor = connection.actor, None
        if actor is None or actor.connection is not connection:
            return
        room = next(
            (
                r
                for r in self.server.rooms.values()
                if r.actors.get(actor.number) is actor
            ),
            None,
        )
        if room is None:
            return
        if inactive and room.player_ttl != 0:
            actor.connection = None
            actor.inactive_since = time.monotonic()
            self._announce_leave(room, actor, inactive=True)
        else:
            self._remove_actor(room, actor)
        if not any(room.active_actors()):
            room.empty_since = time.monotonic()
        self.server.room_changed(room)

    def _remove_actor(self, room: Room, actor: Actor) -> None:
        del room.actors[actor.number]
        if room.cleanup_cache_on_leave:
            room.cache = [e for e in room.cache if e.sender != actor.number]
        self._announce_leave(room, actor, inactive=False)

    def _announce_leave(self, room: Room, actor: Actor, *, inactive: bool) -> None:
        params: Parameters = {
            ParameterKey.ActorNr: Int32Parameter(actor.number),
            ParameterKey.ActorList: room.actor_list(),
        }
        if inactive:
            params[ParameterKey.IsInactive] = BooleanParameter(value=True)
        if room.master_client_id == actor.number:
            active = [a.number for a in room.active_actors()]
            room.master_client_id = min(active, default=0)
            params[ParameterKey.MasterClientId] = Int32Parameter(room.master_client_id)
        if room.suppress_room_events:
            return
        for other in room.active_actors():
            assert other.connection is not None  # noqa: S101 -- active
            other.connection.send_event(EventCode.Leave, params)

    # -- in the room -----------------------------------------------------------------

    def _room_of(self, connection: Connection) -> tuple[Room, Actor]:
        actor = connection.actor
        assert actor is not None  # noqa: S101 -- checked by handle()
        room = next(
            r for r in self.server.rooms.values() if r.actors.get(actor.number) is actor
        )
        return room, actor

    def _raise_event(
        self,
        connection: Connection,
        operation: int,  # noqa: ARG002 -- one signature for all operations
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        room, sender = self._room_of(connection)
        code_param = params.get(ParameterKey.Code)
        code = int(code_param.value) if code_param is not None else 0
        event: Parameters = {ParameterKey.ActorNr: Int32Parameter(sender.number)}
        if (data := params.get(ParameterKey.Data)) is not None:
            event[ParameterKey.Data] = data

        cache_param = params.get(ParameterKey.Cache)
        cache = int(cache_param.value) if cache_param is not None else 0
        if cache == EventCaching.RemoveFromRoomCache:
            self._remove_from_cache(room, code, params)
            return
        if cache == EventCaching.RemoveFromRoomCacheForActorsLeft:
            room.cache = [
                e for e in room.cache if e.sender in room.actors or not e.sender
            ]
            return
        if cache in {EventCaching.AddToRoomCache, EventCaching.AddToRoomCacheGlobal}:
            room.cache.append(
                CachedEvent(
                    0 if cache == EventCaching.AddToRoomCacheGlobal else sender.number,
                    code,
                    {k: v for k, v in event.items() if k != ParameterKey.ActorNr},
                )
            )

        for receiver in self._receivers(room, sender, params):
            assert receiver.connection is not None  # noqa: S101 -- active
            receiver.connection.send_event(code, event, encrypt=encrypted)

    @staticmethod
    def _receivers(room: Room, sender: Actor, params: Parameters) -> list[Actor]:
        if (targets := params.get(ParameterKey.ActorList)) is not None:
            numbers = {int(n) for n in to_python(targets)}
            return [a for a in room.active_actors() if a.number in numbers]
        if (group := params.get(ParameterKey.Group)) is not None and int(group.value):
            return [
                a
                for a in room.active_actors()
                if a is not sender and a.receives_group(int(group.value))
            ]
        receivers = params.get(ParameterKey.ReceiverGroup)
        match int(receivers.value) if receivers is not None else ReceiverGroup.Others:
            case ReceiverGroup.All:
                return list(room.active_actors())
            case ReceiverGroup.MasterClient:
                master = room.actors.get(room.master_client_id)
                return [master] if master is not None and master.is_active else []
            case _:
                return [a for a in room.active_actors() if a is not sender]

    @staticmethod
    def _remove_from_cache(room: Room, code: int, params: Parameters) -> None:
        senders = params.get(ParameterKey.ActorList)
        sender_set = {int(n) for n in to_python(senders)} if senders else None
        data = params.get(ParameterKey.Data)
        data_filter = to_python(data) if data is not None else None

        def matches(event: CachedEvent) -> bool:
            if code and event.code != code:
                return False
            if sender_set is not None and event.sender not in sender_set:
                return False
            if isinstance(data_filter, dict):
                cached = event.params.get(ParameterKey.Data)
                content = to_python(cached) if cached is not None else None
                return isinstance(content, dict) and all(
                    content.get(k) == v for k, v in data_filter.items()
                )
            return True

        room.cache = [e for e in room.cache if not matches(e)]

    def _set_properties(
        self,
        connection: Connection,
        operation: int,  # noqa: ARG002 -- one signature for all operations
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        room, sender = self._room_of(connection)
        properties = params.get(ParameterKey.Properties)
        if not isinstance(properties, DictionaryParameterBase):
            connection.respond(
                OperationCode.SetProperties,
                return_code=ErrorCode.InvalidOperation,
                message="missing properties",
                encrypt=encrypted,
            )
            return
        target_param = params.get(ParameterKey.ActorNr)
        target_nr = int(target_param.value) if target_param is not None else 0
        target = room.actors.get(target_nr) if target_nr else None
        expected = params.get(ParameterKey.ExpectedValues)
        error = None
        if target_nr and target is None:
            error = (ErrorCode.InvalidOperation, f"no actor {target_nr}")
        elif isinstance(expected, DictionaryParameterBase) and not (
            self._matches(room, target, expected)
        ):
            error = (CAS_FAILED, "expected values don't match")
        if error is not None:
            connection.respond(
                OperationCode.SetProperties,
                return_code=error[0],
                message=error[1],
                encrypt=encrypted,
            )
            return

        if target is not None:
            target.properties.update(properties)
        else:
            self._update_room(room, properties)
        connection.respond(OperationCode.SetProperties, encrypt=encrypted)

        broadcast = params.get(ParameterKey.Broadcast)
        with_cas = expected is not None
        if not with_cas and not (broadcast is not None and broadcast.value):
            return
        event: Parameters = {
            ParameterKey.Properties: properties,
            ParameterKey.TargetActorNr: Int32Parameter(target_nr),
            ParameterKey.ActorNr: Int32Parameter(sender.number),
        }
        for receiver in room.active_actors():
            if receiver is sender and not with_cas:
                continue
            assert receiver.connection is not None  # noqa: S101 -- active
            receiver.connection.send_event(EventCode.PropertiesChanged, event)
        if target is None:
            self.server.room_changed(room)

    @staticmethod
    def _matches(
        room: Room, target: Actor | None, expected: DictionaryParameterBase[Any, Any]
    ) -> bool:
        if target is not None:
            return target.properties.matches(expected)
        plain = to_python(expected)
        if MASTER_CLIENT_ID in plain and plain.pop(MASTER_CLIENT_ID) != (
            room.master_client_id
        ):
            return False
        return all(room.properties.get(k) == v for k, v in plain.items())

    @staticmethod
    def _update_room(room: Room, properties: DictionaryParameterBase[Any, Any]) -> None:
        table: dict[Any, Any] = {}
        for key, value in properties.items():
            plain = to_python(key)
            if plain == MASTER_CLIENT_ID:
                actor = room.actors.get(int(to_python(value)))
                if actor is not None and actor.is_active:
                    room.master_client_id = actor.number
            elif plain not in _READ_ONLY:
                table[key] = value
        room.properties.update(HashtableParameter(table))

    def _get_properties(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,  # noqa: ARG002 -- everything is returned
        *,
        encrypted: bool,
    ) -> None:
        room, _ = self._room_of(connection)
        connection.respond(
            operation,
            {
                ParameterKey.GameProperties: room.game_properties(),
                ParameterKey.PlayerProperties: HashtableParameter(
                    {
                        Int32Parameter(a.number): a.properties_for(room)
                        for a in room.actors.values()
                    }
                ),
            },
            encrypt=encrypted,
        )

    def _change_groups(
        self,
        connection: Connection,
        operation: int,  # noqa: ARG002 -- one signature for all operations
        params: Parameters,
        *,
        encrypted: bool,  # noqa: ARG002 -- there is no response
    ) -> None:
        actor = connection.actor
        assert actor is not None  # noqa: S101 -- checked by handle()
        if (remove := params.get(ParameterKey.Remove)) is not None:
            groups = list(to_python(remove))
            if groups:
                actor.groups.difference_update(groups)
            else:
                actor.groups = {0}
                actor.all_groups = False
        if (add := params.get(ParameterKey.Add)) is not None:
            groups = list(to_python(add))
            if groups:
                actor.groups.update(groups)
            else:
                actor.all_groups = True
