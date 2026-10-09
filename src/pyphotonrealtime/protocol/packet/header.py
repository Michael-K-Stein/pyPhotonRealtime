from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.consts import MSG_MAGIC
from pyphotonrealtime.protocol.enum_lookups import get_command_name

if TYPE_CHECKING:
    from io import BytesIO

# 4-byte length + 2-byte peer id + magic + command.
HEADER_SIZE = 9


class PhotonDataPacketHeader:
    peer_id: bytes
    msg_magic: int
    command: CommandCode
    packet_length: int | None

    def __init__(
        self,
        command_code: CommandCode,
        peer_id: bytes = b"\x00\x01",
        msg_magic: int = MSG_MAGIC,
        packet_length: int | None = None,
    ) -> None:
        self.peer_id = peer_id
        self.msg_magic = msg_magic
        self.command = command_code
        self.packet_length = packet_length

    def get_command_code(self) -> CommandCode:
        return self.command

    def get_command_name(self) -> str:
        return get_command_name(self.command)

    @staticmethod
    def from_bytes(data: BytesIO) -> PhotonDataPacketHeader:
        length = int.from_bytes(data.read(4), byteorder="big", signed=False)

        packet_length = length
        peer_id = data.read(2)
        msg_magic = int.from_bytes(data.read(1))
        command = cast("CommandCode", int.from_bytes(data.read(1)))

        return PhotonDataPacketHeader(
            peer_id=peer_id,
            msg_magic=msg_magic,
            command_code=command,
            packet_length=packet_length,
        )

    def data_offset(self) -> int:
        return HEADER_SIZE

    def is_encrypted(self) -> bool:
        return self.get_command_code() in (
            CommandCode.EncryptedOperation,
            CommandCode.EncryptedOperationResponse,
            CommandCode.EncryptedEvent,
        )

    def is_response(self) -> bool:
        return self.get_command_code() in (
            CommandCode.KeyExchangeResponse,
            CommandCode.EncryptedOperationResponse,
            CommandCode.OperationResponse,
            CommandCode.InitResponse,
        )

    def is_operation(self) -> bool:
        return self.get_command_code() in (
            CommandCode.Operation,
            CommandCode.OperationResponse,
            CommandCode.EncryptedOperation,
            CommandCode.EncryptedOperationResponse,
            CommandCode.Event,
            CommandCode.EncryptedEvent,
            CommandCode.DisconnectMessage,
            CommandCode.KeyExchangeRequest,
            CommandCode.KeyExchangeResponse,
        )

    def serialize(self, data_length: int | None = None) -> bytes:
        command_byte = self.get_command_code().to_bytes(1)
        routing_header = self.peer_id + self.msg_magic.to_bytes(1) + command_byte

        total_length = (
            (self.data_offset() + data_length)
            if data_length is not None
            else self.packet_length
        )
        if total_length is None:
            msg = "Packet length is unknown!"
            raise ValueError(msg)

        tcp_envelope = total_length.to_bytes(4, byteorder="big")

        return tcp_envelope + routing_header

    @staticmethod
    def size() -> int:
        return HEADER_SIZE
