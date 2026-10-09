from typing import Any, Dict, Optional


class Player:
    def __init__(
        self, actor_number: int, nick_name: str = "", is_local: bool = False
    ) -> None:
        self.actor_number = actor_number
        self.nick_name = nick_name
        self.is_local = is_local
        self.user_id: Optional[str] = None
        self.is_inactive = False
        self.custom_properties: Dict[Any, Any] = {}

    def set_custom_properties(self, properties: Dict[Any, Any]) -> bool:
        """TODO: OpSetProperties with this player's actor number."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"Player(#{self.actor_number} {self.nick_name!r})"
