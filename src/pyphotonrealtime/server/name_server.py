"""Name Server: region list and authentication."""

from __future__ import annotations

import secrets
import uuid
from typing import TYPE_CHECKING, Any, cast, override

from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.param.dict_param import DictionaryParameter
from pyphotonrealtime.protocol.param.int8_param import Int8Parameter
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.realtime._convert import string_array
from pyphotonrealtime.realtime.error_code import ErrorCode
from pyphotonrealtime.server.handler import RoleHandler

if TYPE_CHECKING:
    from pyphotonrealtime.peer import Parameters
    from pyphotonrealtime.server.connection import Connection
    from pyphotonrealtime.server.server import PhotonServer

CLUSTER = "default"
# EncryptionData keys and the one mode we support (EncryptionMode in the SDKs).
_ENCRYPTION_MODE = 0
_ENCRYPTION_SECRET1 = 1
_PAYLOAD_ENCRYPTION = 0
_SECRET_SIZE = 32


class NameServer(RoleHandler):
    """Lists the regions (all served by this process) and issues tokens."""

    def __init__(self, server: PhotonServer) -> None:
        """Serve on behalf of ``server``."""
        super().__init__(server)
        self.operations = {
            OperationCode.GetRegions: self._get_regions,
            OperationCode.Authenticate: self._authenticate,
            OperationCode.AuthOnce: self._authenticate,
        }

    @override
    def refuse(self, connection: Connection, operation: int) -> tuple[int, str] | None:
        if not self.server.check_app_id(connection):
            return ErrorCode.InvalidAuthentication, "invalid app id"
        return None

    def _get_regions(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,  # noqa: ARG002 -- the app id came with the init
        *,
        encrypted: bool,
    ) -> None:
        address = self.server.master_server_address
        connection.respond(
            operation,
            {
                ParameterKey.Region: string_array(self.server.regions),
                ParameterKey.Address: string_array(
                    [address] * len(self.server.regions)
                ),
            },
            encrypt=encrypted,
        )

    def _authenticate(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        region = params.get(ParameterKey.Region)
        if region is not None and str(region.value).lower().split("/")[0] not in {
            r.lower() for r in self.server.regions
        }:
            connection.respond(
                operation,
                return_code=ErrorCode.InvalidRegion,
                message=f"unknown region {region.value!r}",
                encrypt=encrypted,
            )
            return
        user_id = params.get(ParameterKey.UserId)
        token, session = self.server.new_session(
            str(user_id.value) if user_id and user_id.value else str(uuid.uuid4())
        )
        session.lobby_stats = bool(
            (stats := params.get(ParameterKey.LobbyStats)) and stats.value
        )
        connection.session = session
        response: Parameters = {
            ParameterKey.Address: StringParameter(self.server.master_server_address),
            ParameterKey.Token: StringParameter(token),
            ParameterKey.UserId: StringParameter(session.user_id),
            ParameterKey.Cluster: StringParameter(CLUSTER),
        }
        if operation == OperationCode.AuthOnce:
            session.secret = secrets.token_bytes(_SECRET_SIZE)
            response[ParameterKey.EncryptionData] = DictionaryParameter(
                cast(
                    "Any",
                    {
                        Int8Parameter(_ENCRYPTION_MODE): Int8Parameter(
                            _PAYLOAD_ENCRYPTION
                        ),
                        Int8Parameter(_ENCRYPTION_SECRET1): Int8SliceParameter(
                            session.secret
                        ),
                    },
                )
            )
        connection.respond(operation, response, encrypt=encrypted)
