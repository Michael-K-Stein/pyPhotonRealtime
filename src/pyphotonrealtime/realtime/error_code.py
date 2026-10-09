"""Operation return codes (``ErrorCode`` in the SDK)."""

from enum import IntEnum


class ErrorCode(IntEnum):
    """Non-zero ``OperationResponse.return_code`` values sent by Photon servers."""

    Ok = 0
    OperationNotAllowedInCurrentState = -3
    InvalidOperation = -2
    InternalServerError = -1
    InvalidAuthentication = 0x7FFF  # 32767
    GameIdAlreadyExists = 0x7FFF - 1  # 32766
    GameFull = 0x7FFF - 2  # 32765
    GameClosed = 0x7FFF - 3  # 32764
    ServerFull = 0x7FFF - 5  # 32762
    UserBlocked = 0x7FFF - 6  # 32761
    NoRandomMatchFound = 0x7FFF - 7  # 32760
    GameDoesNotExist = 0x7FFF - 9  # 32758
    MaxCcuReached = 0x7FFF - 10  # 32757
    InvalidRegion = 0x7FFF - 11  # 32756
    CustomAuthenticationFailed = 0x7FFF - 12  # 32755
    AuthenticationTicketExpired = 0x7FF1  # 32753
