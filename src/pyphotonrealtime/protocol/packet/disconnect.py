from __future__ import annotations

from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol

if TYPE_CHECKING:
    from io import BytesIO

    from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader
    from pyphotonrealtime.protocol.packet.operation_payload import PhotonPacketPayload


class DisconnectMessagePacket(PhotonOperationPacket):
    def __init__(
        self,
        header: PhotonDataPacketHeader,
        payload: PhotonPacketPayload,
        aes_key: bytes | None = None,
    ) -> None:
        super().__init__(header=header, payload=payload, aes_key=aes_key)

    @classmethod
    def from_bytes(
        cls,
        data: BytesIO,
        *,
        header: PhotonDataPacketHeader | None = None,
        aes_key: bytes | None = None,
        protocol: SerializationProtocol = SerializationProtocol.V16,
    ) -> Self:
        return super().from_bytes(
            data, header=header, aes_key=aes_key, protocol=protocol
        )

    def log(self) -> list[str]:
        return [
            (
                f"Command: {self.get_header().get_command_name()} "
                f"{self.get_payload().get_operation_name()}"
            ),
            f"Reason: {self.get_payload().get_debug_message()}",
        ]
