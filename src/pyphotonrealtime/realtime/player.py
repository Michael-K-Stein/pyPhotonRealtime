"""Players (actors) in a room."""

from typing import Any


class Player:
    """A player in the current room; ``is_local`` marks this client."""

    def __init__(
        self, actor_number: int, nick_name: str = "", *, is_local: bool = False
    ) -> None:
        """Create a player with no custom properties."""
        self.actor_number = actor_number
        self.nick_name = nick_name
        self.is_local = is_local
        self.user_id: str | None = None
        self.is_inactive = False
        self.custom_properties: dict[Any, Any] = {}

    def set_custom_properties(self, properties: dict[Any, Any]) -> bool:
        """Merge ``properties`` into this player's properties on the server.

        Returns:
            Whether the operation was queued.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        """Short debug form: actor number and nickname."""
        return f"Player(#{self.actor_number} {self.nick_name!r})"
