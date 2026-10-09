"""Base class of the Name, Master and Game Server logic."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.realtime.error_code import ErrorCode

if TYPE_CHECKING:
    from pyphotonrealtime.peer import Parameters
    from pyphotonrealtime.protocol.param.base import ParameterBase
    from pyphotonrealtime.server.connection import Connection
    from pyphotonrealtime.server.server import PhotonServer


class Operation(Protocol):
    """Answers one operation code."""

    def __call__(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        """Answer ``operation`` sent by ``connection``."""


class RoleHandler:
    """Answers the operations sent to one of the three servers.

    Subclasses fill ``operations`` and may turn some away in ``refuse``.
    """

    def __init__(self, server: PhotonServer) -> None:
        """Serve on behalf of ``server``, which holds the shared state."""
        self.server = server
        self.operations: dict[int, Operation] = {}

    def refuse(self, connection: Connection, operation: int) -> tuple[int, str] | None:
        """Why ``connection`` may not send ``operation`` now, if it may not.

        Returns:
            Return code and message, or None to go ahead.
        """
        del connection, operation
        return None

    def handle(
        self,
        connection: Connection,
        operation: int,
        params: Parameters,
        *,
        encrypted: bool,
    ) -> None:
        """Answer one operation, or refuse it with an error response."""
        answer = self.operations.get(operation)
        if answer is None:
            error: tuple[int, str] | None = (
                ErrorCode.InvalidOperation,
                f"operation {operation} is not supported",
            )
        else:
            error = self.refuse(connection, operation)
        if error is not None:
            connection.respond(
                operation, return_code=error[0], message=error[1], encrypt=encrypted
            )
        elif answer is not None:
            answer(connection, operation, params, encrypted=encrypted)

    def disconnected(self, connection: Connection) -> None:
        """Clean up after a client that went away."""

    def tick(self, now: float) -> None:
        """Run timers; called every loop iteration."""

    def init_token(
        self, connection: Connection, token: ParameterBase[Any] | None
    ) -> None:
        """Authenticate with the token a client sent in its init (AuthOnce).

        The client sends no Authenticate operation then; the response is ours
        to send unasked.
        """
        if self.authenticate_with_token(
            connection, {ParameterKey.Token: token} if token else {}, encrypted=False
        ):
            self.authenticated(connection, encrypted=False)

    def authenticated(self, connection: Connection, *, encrypted: bool) -> None:
        """Answer a successful Authenticate."""
        connection.respond(OperationCode.Authenticate, encrypt=encrypted)

    def authenticate_with_token(
        self, connection: Connection, params: Parameters, *, encrypted: bool
    ) -> bool:
        """Accept the token issued by the Name Server, or reject the client.

        Returns:
            Whether the client is now authenticated.
        """
        token = params.get(ParameterKey.Token)
        session = self.server.sessions.get(str(token.value)) if token else None
        if session is None:
            connection.respond(
                OperationCode.Authenticate,
                return_code=ErrorCode.InvalidAuthentication,
                message="invalid token",
                encrypt=encrypted,
            )
            return False
        connection.session = session
        if connection.aes_key is None and session.secret is not None:
            connection.aes_key = session.secret  # AuthOnce: no key exchange here.
        return True
