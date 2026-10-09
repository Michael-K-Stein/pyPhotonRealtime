from enum import IntEnum


class CommandCode(IntEnum):
    Init = 0
    InitResponse = 1

    Operation = 2
    OperationResponse = 3

    Event = 4
    DisconnectMessage = 5

    KeyExchangeRequest = 6
    KeyExchangeResponse = 7
    # Internal operations; the key exchange is one of them.
    InternalOperationRequest = 6
    InternalOperationResponse = 7

    Message = 8
    RawMessage = 9

    EncryptedOperation = 0x82
    EncryptedOperationResponse = 0x83

    EncryptedEvent = 0x84

    Unknown = 0xFF


class InternalOperationCode(IntEnum):
    """Operation codes of internal operations (``PhotonCodes`` in the SDKs)."""

    InitEncryption = 0
    Ping = 1
    """Keep-alive over WebSockets, which can't send TCP's ping packets."""


ENCRYPTED_FLAG = 0x80


def is_encrypted(command_code: CommandCode) -> bool:
    return command_code.value > ENCRYPTED_FLAG
