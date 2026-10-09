from enum import IntEnum


def _as_code(value: object) -> int:
    if not isinstance(value, int):
        msg = f"Event codes are ints, got {type(value).__name__}"
        raise TypeError(msg)
    return value


class PrisonArchitectEventCode(IntEnum):
    """Reverse-engineered custom Photon event codes for Prison Architect.

    Photon custom events are strictly in the 1-199 range.
    """

    PlayerJoin_Unknown = 2  # Suspected Player Join
    PasswordAuthRequest = 5
    KickPlayer = 6  # Soft kick, just asks the client to leave
    WorldUpdate = 9
    TickSpeedUpdate = 96

    # Placeholders for identified but unmapped events
    Unknown1 = 1
    Unknown3 = 3
    Unknown4 = 4
    Unknown8 = 8
    Unknown13 = 13
    Unknown14 = 14
    Unknown47 = 47
    Unknown89 = 89
    Unknown95 = 95
    Unknown118 = 118

    @classmethod
    def _missing_(cls, value: object) -> int:
        """Map unknown codes to plain ints so undocumented events don't crash."""
        return _as_code(value)


CUSTOM_EVENT_CODE_MAX = 200


class EventCode(IntEnum):
    # In-room events from the Game Server.
    Join = 0xFF  # 255
    Leave = 0xFE  # 254
    PropertiesChanged = 0xFD  # 253
    ErrorInfo = 0xFB  # 251
    CacheSliceChanged = 0xFA  # 250

    GameList = 0xE6  # 230
    GameListUpdate = 0xE5
    QueueState = 0xE4
    AppStats = 0xE2  # 226
    GameServerOffline = 0xE1
    LobbyStats = 0xE0
    AuthEvent = 0xDF

    Event = 0xFF  # Alias of Join.

    @classmethod
    def _missing_(cls, value: object) -> int:
        """Map unknown codes to plain ints so undocumented events don't crash."""
        code = _as_code(value)
        if 0 < code <= CUSTOM_EVENT_CODE_MAX:
            return PrisonArchitectEventCode(code)
        return code
