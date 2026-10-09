"""Master Server: lobbies, room lists and matchmaking."""

from __future__ import annotations

import random
import uuid
from typing import TYPE_CHECKING, Any, override

from pyphotonrealtime.protocol.event_code import EventCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.param.bool_param import BooleanParameter
from pyphotonrealtime.protocol.param.hashtable_param import HashtableParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int32_param import Int32Parameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.parameter_type import ParameterType
from pyphotonrealtime.protocol.param.slice_param import SliceParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.realtime._convert import string_array, to_python
from pyphotonrealtime.realtime.error_code import ErrorCode
from pyphotonrealtime.realtime.lobby import (
    JoinMode,
    LobbyType,
    MatchmakingMode,
    TypedLobby,
)
from pyphotonrealtime.server.connection import Role
from pyphotonrealtime.server.handler import RoleHandler
from pyphotonrealtime.server.rooms import MAX_PLAYERS

if TYPE_CHECKING:
    from pyphotonrealtime.peer import Parameters
    from pyphotonrealtime.server.connection import Connection
    from pyphotonrealtime.server.rooms import Room
    from pyphotonrealtime.server.server import PhotonServer

# Lobbies that get a room list on join (SQL and async lobbies don't).
_LISTING_LOBBIES = {LobbyType.Default}


def lobby_of(params: Parameters) -> TypedLobby:
    """The lobby named by an operation's LobbyName/LobbyType parameters.

    Returns:
        The lobby; the default one if none is named.
    """
    name = params.get(ParameterKey.LobbyName)
    kind = params.get(ParameterKey.LobbyType)
    return TypedLobby(
        str(name.value) if name is not None else "",
        LobbyType(int(kind.value)) if kind is not None else LobbyType.Default,
    )


def _flag(params: Parameters, key: int) -> bool:
    value = params.get(key)
    return bool(value is not None and value.value)


class MasterServer(RoleHandler):
    """Authenticates tokens, lists rooms and sends clients to the Game Server."""

    def __init__(self, server: PhotonServer) -> None:
        """Serve on behalf of ``server``."""
        super().__init__(server)
        self.operations = {
            OperationCode.Authenticate: self._authenticate,
            OperationCode.JoinLobby: self._join_lobby,
            OperationCode.LeaveLobby: self._leave_lobby,
            OperationCode.GetGameList: self._get_game_list,
            OperationCode.CreateGame: self._create,
            OperationCode.JoinGame: self._join,
            OperationCode.JoinRandomGame: self._join_random,
            OperationCode.FindFriends: self._find_friends,
            OperationCode.LobbyStats: self._get_lobby_stats,
        }

    @override
    def refuse(self, connection: Connection, operation: int) -> tuple[int, str] | None:
        if operation != OperationCode.Authenticate and not connection.authenticated:
            return ErrorCode.OperationNotAllowedInCurrentState, "authenticate first"
        return None

    def _join_lobby(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        connection.lobby = lobby_of(params)
        connection.respond(operation, encrypt=encrypted)
        if connection.lobby.type in _LISTING_LOBBIES:
            connection.send_event(EventCode.GameList, self._game_list(connection.lobby))

    def _leave_lobby(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,  # noqa: ARG002 -- nothing to read
        *,
        encrypted: bool,
    ) -> None:
        connection.lobby = None
        connection.respond(operation, encrypt=encrypted)

    def _get_game_list(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        connection.respond(
            operation, self._game_list(lobby_of(params)), encrypt=encrypted
        )

    def _get_lobby_stats(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,  # noqa: ARG002 -- every lobby is reported
        *,
        encrypted: bool,
    ) -> None:
        connection.respond(operation, self._lobby_stats(), encrypt=encrypted)

    def _authenticate(
        self,
        connection: Connection,
        operation: int,  # noqa: ARG002 -- one signature for all operations
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        if ParameterKey.Token not in params:
            # Straight to the Master Server, skipping the Name Server.
            if not self.server.check_app_id(connection):
                connection.respond(
                    OperationCode.Authenticate,
                    return_code=ErrorCode.InvalidAuthentication,
                    message="invalid app id",
                    encrypt=encrypted,
                )
                return
            user_id = params.get(ParameterKey.UserId)
            _, session = self.server.new_session(
                str(user_id.value) if user_id and user_id.value else str(uuid.uuid4())
            )
            session.lobby_stats = _flag(params, ParameterKey.LobbyStats)
            connection.session = session
        elif not self.authenticate_with_token(connection, params, encrypted=encrypted):
            return
        self.authenticated(connection, encrypted=encrypted)

    @override
    def authenticated(self, connection: Connection, *, encrypted: bool) -> None:
        session = connection.session
        assert session is not None  # noqa: S101 -- just authenticated
        connection.respond(
            OperationCode.Authenticate,
            {
                ParameterKey.Token: StringParameter(session.token),
                ParameterKey.UserId: StringParameter(session.user_id),
            },
            encrypt=encrypted,
        )
        connection.send_event(EventCode.AppStats, self._app_stats())
        if session.lobby_stats:
            connection.send_event(EventCode.LobbyStats, self._lobby_stats())

    # -- room lists and stats -------------------------------------------------------

    def _game_list(self, lobby: TypedLobby) -> Parameters:
        return {
            ParameterKey.GameList: HashtableParameter(
                {
                    StringParameter(room.name): room.lobby_listing()
                    for room in self.server.rooms.values()
                    if room.lobby == lobby and room.listed
                }
            )
        }

    def _app_stats(self) -> Parameters:
        rooms = self.server.rooms.values()
        return {
            ParameterKey.MasterPeerCount: Int32Parameter(
                sum(1 for _ in self.server.connections_of(Role.MasterServer))
            ),
            ParameterKey.PeerCount: Int32Parameter(
                sum(sum(1 for _ in r.active_actors()) for r in rooms)
            ),
            ParameterKey.GameCount: Int32Parameter(len(self.server.rooms)),
        }

    def _lobby_stats(self) -> Parameters:
        lobbies: dict[TypedLobby, list[Room]] = {TypedLobby(): []}
        for room in self.server.rooms.values():
            lobbies.setdefault(room.lobby, []).append(room)

        def ints(values: list[int]) -> SliceParameter[Int32Parameter]:
            return SliceParameter(
                [Int32Parameter(v) for v in values],
                element_type=ParameterType.Int32Type,
            )

        return {
            ParameterKey.LobbyName: string_array(lobby.name for lobby in lobbies),
            ParameterKey.LobbyType: SliceParameter(
                [Int8Parameter(lobby.type) for lobby in lobbies],
                element_type=ParameterType.Int8Type,
            ),
            ParameterKey.PeerCount: ints(
                [sum(r.player_count for r in rooms) for rooms in lobbies.values()]
            ),
            ParameterKey.GameCount: ints([len(rooms) for rooms in lobbies.values()]),
        }

    # -- matchmaking ------------------------------------------------------------------

    def _send_to_game_server(
        self, connection: Connection, operation: int, room_name: str, *, encrypted: bool
    ) -> None:
        connection.respond(
            operation,
            {
                ParameterKey.GameId: StringParameter(room_name),
                ParameterKey.Address: StringParameter(self.server.game_server_address),
            },
            encrypt=encrypted,
        )

    def _create(
        self,
        connection: Connection,
        operation: int,  # noqa: ARG002 -- one signature for all operations
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        name = params.get(ParameterKey.GameId)
        room_name = str(name.value) if name is not None else uuid.uuid4().hex
        if room_name in self.server.rooms:
            connection.respond(
                OperationCode.CreateGame,
                return_code=ErrorCode.GameIdAlreadyExists,
                message=f"room {room_name!r} already exists",
                encrypt=encrypted,
            )
            return
        self._send_to_game_server(
            connection, OperationCode.CreateGame, room_name, encrypted=encrypted
        )

    def _join(
        self,
        connection: Connection,
        operation: int,  # noqa: ARG002 -- one signature for all operations
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        assert connection.session is not None  # noqa: S101 -- authenticated
        name = params.get(ParameterKey.GameId)
        room = self.server.rooms.get(str(name.value)) if name is not None else None
        mode = params.get(ParameterKey.JoinMode)
        join_mode = JoinMode(int(mode.value)) if mode is not None else JoinMode.Default
        error: tuple[int, str] | None = None
        if name is None:
            error = (ErrorCode.InvalidOperation, "missing room name")
        elif room is None:
            if join_mode != JoinMode.CreateIfNotExists:
                error = (ErrorCode.GameDoesNotExist, "room does not exist")
        elif room.find_user(connection.session.user_id) is None:
            error = _join_error(room, connection.session.user_id)
        if error is not None:
            connection.respond(
                OperationCode.JoinGame,
                return_code=error[0],
                message=error[1],
                encrypt=encrypted,
            )
            return
        assert name is not None  # noqa: S101 -- checked above
        self._send_to_game_server(
            connection, OperationCode.JoinGame, str(name.value), encrypted=encrypted
        )

    def _join_random(
        self,
        connection: Connection,
        operation: int,  # noqa: ARG002 -- one signature for all operations
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        assert connection.session is not None  # noqa: S101 -- authenticated
        user_id = connection.session.user_id
        lobby = lobby_of(params)
        expected = params.get(ParameterKey.GameProperties)
        candidates = [
            room
            for room in self.server.rooms.values()
            if room.lobby == lobby
            and room.listed
            and _join_error(room, user_id) is None
            and (expected is None or _matches_filter(room, to_python(expected)))
        ]
        if candidates:
            mode = params.get(ParameterKey.MatchMakingType)
            room = (
                random.choice(candidates)  # noqa: S311 -- not crypto
                if mode is not None
                and int(mode.value) == MatchmakingMode.RandomMatching
                else candidates[0]
            )
            self._send_to_game_server(
                connection, OperationCode.JoinRandomGame, room.name, encrypted=encrypted
            )
            return
        mode = params.get(ParameterKey.JoinMode)
        if mode is not None and int(mode.value) == JoinMode.CreateIfNotExists:
            name = params.get(ParameterKey.GameId)
            self._send_to_game_server(
                connection,
                OperationCode.JoinRandomGame,
                str(name.value) if name is not None else uuid.uuid4().hex,
                encrypted=encrypted,
            )
            return
        connection.respond(
            OperationCode.JoinRandomGame,
            return_code=ErrorCode.NoRandomMatchFound,
            message="no match found",
            encrypt=encrypted,
        )

    def _find_friends(
        self,
        connection: Connection,
        operation: int,  # noqa: ARG002 -- one signature for all operations
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        requested = params.get(ParameterKey.FindFriendsRequestList)
        user_ids = [str(u) for u in to_python(requested)] if requested else []
        online = {
            c.session.user_id: c
            for c in self.server.connections
            if c.session is not None
        }
        rooms = {
            actor.user_id: room.name
            for room in self.server.rooms.values()
            for actor in room.active_actors()
        }
        connection.respond(
            OperationCode.FindFriends,
            {
                ParameterKey.FindFriendsRequestList: SliceParameter(
                    [BooleanParameter(value=u in online) for u in user_ids],
                    element_type=ParameterType.BooleanType,
                ),
                ParameterKey.FindFriendsResponseRoomIdList: string_array(
                    rooms.get(u, "") for u in user_ids
                ),
            },
            encrypt=encrypted,
        )


def _join_error(room: Room, user_id: str) -> tuple[int, str] | None:
    if not room.is_open:
        return ErrorCode.GameClosed, "room is closed"
    if room.is_full_for(user_id):
        return ErrorCode.GameFull, "room is full"
    return None


def _matches_filter(room: Room, expected: dict[Any, Any]) -> bool:
    return all(
        room.properties.get(key) == value
        or (key == MAX_PLAYERS and room.max_players == value)
        for key, value in expected.items()
    )
