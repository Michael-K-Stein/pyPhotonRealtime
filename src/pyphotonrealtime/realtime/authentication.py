"""Authentication values and modes (``AuthenticationValues``, ``AuthModeOption``)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, auto
from typing import Any
from urllib.parse import quote


class AuthMode(IntEnum):
    """How the client authenticates on each server it hops to."""

    Auth = auto()
    """Authenticate on every server; each connection does its own key exchange."""
    AuthOnce = auto()
    """Authenticate once on the Name Server; later servers only get the token."""


class CustomAuthenticationType(IntEnum):
    """Custom authentication provider (``ClientAuthenticationType``)."""

    Custom = 0
    Steam = 1
    Facebook = 2
    Oculus = 3
    PlayStation4 = 4
    Xbox = 5
    Viveport = 10
    NintendoSwitch = 11
    PlayStation5 = 12
    Epic = 13
    FacebookGaming = 15
    NoAuth = 255
    """No custom authentication (the SDK's ``None``)."""


@dataclass(slots=True, kw_only=True)
class AuthenticationValues:
    """Who this client is, and how to prove it to a custom auth provider."""

    user_id: str | None = None
    auth_type: CustomAuthenticationType = CustomAuthenticationType.NoAuth
    auth_get_parameters: str = ""
    """Query string forwarded to the auth provider, e.g. ``user=a&token=b``."""
    auth_post_data: str | bytes | None = None
    """Body forwarded to the auth provider."""
    token: Any = None
    """Set by the Name Server after authentication; reused on later servers."""

    def add_auth_parameter(self, key: str, value: str) -> None:
        """Append a URL-encoded ``key=value`` pair to ``auth_get_parameters``."""
        separator = "&" if self.auth_get_parameters else ""
        self.auth_get_parameters += f"{separator}{quote(key)}={quote(value)}"
