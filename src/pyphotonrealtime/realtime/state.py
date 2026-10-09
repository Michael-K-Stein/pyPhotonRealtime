from enum import Enum, auto


class ServerType(Enum):
    NameServer = auto()
    MasterServer = auto()
    GameServer = auto()


class ClientState(Enum):
    """Workflow states of ``RealtimeClient`` (same names as the C# SDK)."""

    PeerCreated = auto()
    ConnectingToNameServer = auto()
    ConnectedToNameServer = auto()
    DisconnectingFromNameServer = auto()
    Authenticating = auto()
    Authenticated = auto()
    ConnectingToMasterServer = auto()
    ConnectedToMasterServer = auto()
    DisconnectingFromMasterServer = auto()
    JoiningLobby = auto()
    JoinedLobby = auto()
    ConnectingToGameServer = auto()
    ConnectedToGameServer = auto()
    DisconnectingFromGameServer = auto()
    Joining = auto()
    Joined = auto()
    Leaving = auto()
    Disconnecting = auto()
    Disconnected = auto()


class DisconnectCause(Enum):
    NoCause = auto()
    ExceptionOnConnect = auto()
    Exception = auto()
    ServerTimeout = auto()
    ClientTimeout = auto()
    DisconnectByServerLogic = auto()
    DisconnectByServerReasonUnknown = auto()
    InvalidAuthentication = auto()
    CustomAuthenticationFailed = auto()
    AuthenticationTicketExpired = auto()
    MaxCcuReached = auto()
    InvalidRegion = auto()
    OperationNotAllowedInCurrentState = auto()
    DisconnectByClientLogic = auto()
