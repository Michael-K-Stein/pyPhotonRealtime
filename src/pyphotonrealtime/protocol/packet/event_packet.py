from __future__ import annotations

from typing import TYPE_CHECKING

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader
    from pyphotonrealtime.protocol.packet.operation_payload import (
        PhotonPacketPayload,
    )


class PhotonEventPacket(PhotonOperationPacket):
    def __init__(
        self,
        header: PhotonDataPacketHeader,
        payload: PhotonPacketPayload,
        aes_key: bytes | None = None,
    ) -> None:
        super().__init__(header=header, payload=payload, aes_key=aes_key)
        if header.get_command_code() not in (
            CommandCode.Event,
            CommandCode.EncryptedEvent,
        ):
            msg = "Packet is not an Event!"
            raise TypeError(msg)
        self._operation_payload = payload
        self._aes_key = aes_key
