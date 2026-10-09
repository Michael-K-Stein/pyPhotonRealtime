"""Players (actors) in a room."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyphotonrealtime.protocol.property_keys import ActorPropertyKey

if TYPE_CHECKING:
    from pyphotonrealtime.realtime.client import RealtimeClient

_PLAYER_NAME = ActorPropertyKey.PlayerName.value.value
_IS_INACTIVE = ActorPropertyKey.IsInactive.value.value
_USER_ID = ActorPropertyKey.UserId.value.value


class Player:
    """A player in the current room; ``is_local`` marks this client."""

    def __init__(
        self,
        actor_number: int,
        nick_name: str = "",
        *,
        is_local: bool = False,
        client: RealtimeClient | None = None,
    ) -> None:
        """Create a player with no custom properties, owned by ``client``."""
        self.actor_number = actor_number
        self.client = client
        self.nick_name = nick_name
        self.is_local = is_local
        self.user_id: str | None = None
        self.is_inactive = False
        self.custom_properties: dict[Any, Any] = {}

    def update(self, properties: dict[Any, Any]) -> None:
        """Apply a (partial) property set as sent by the server.

        Byte keys are well-known properties; string keys are custom ones,
        where a ``None`` value deletes the key.
        """
        for key, value in properties.items():
            if isinstance(key, str):
                if value is None:
                    self.custom_properties.pop(key, None)
                else:
                    self.custom_properties[key] = value
            elif key == _PLAYER_NAME:
                self.nick_name = str(value)
            elif key == _IS_INACTIVE:
                self.is_inactive = bool(value)
            elif key == _USER_ID:
                self.user_id = str(value)

    def set_custom_properties(
        self,
        properties: dict[Any, Any],
        expected: dict[Any, Any] | None = None,
    ) -> bool:
        """Merge ``properties`` into this player's properties on the server.

        See :meth:`RealtimeClient.op_set_properties_of_actor`.

        Returns:
            Whether the operation was queued.
        """
        if self.client is None:
            return False
        return self.client.op_set_properties_of_actor(
            self.actor_number, properties, expected
        )

    def __repr__(self) -> str:
        """Short debug form: actor number and nickname."""
        return f"Player(#{self.actor_number} {self.nick_name!r})"
