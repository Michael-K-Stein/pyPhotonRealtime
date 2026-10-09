from __future__ import annotations

from abc import ABC
from struct import pack
from typing import TYPE_CHECKING, cast

from pyphotonrealtime.protocol.packet.format import PacketFormat
from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader

if TYPE_CHECKING:
    from io import BytesIO


class PhotonPacket(ABC):
    _packet_format: PacketFormat

    def __init__(self, packet_format: PacketFormat = PacketFormat.Data) -> None:
        self._packet_format = packet_format

    @classmethod
    def from_bytes(cls, data: BytesIO) -> PhotonPacket:
        packet_format = cast("PacketFormat", int.from_bytes(data.read(1)))
        return cls(packet_format)

    def serialize(self) -> bytes:
        format_val = (
            self._packet_format.value
            if hasattr(self._packet_format, "value")
            else self._packet_format
        )
        return pack(">B", format_val)

    def get_format(self) -> PacketFormat:
        return self._packet_format

    def log(self) -> list[str]:
        return [
            f"Abstract Packet {self.get_format()}",
        ]


class PhotonDataPacket(PhotonPacket):
    _header: PhotonDataPacketHeader
    _data_length: int | None

    def __init__(self, header: PhotonDataPacketHeader) -> None:
        super().__init__(PacketFormat.Data)
        self._header = header
        self._data_length = None

    @classmethod
    def from_bytes(cls, data: BytesIO) -> PhotonDataPacket:
        return cls(PhotonDataPacketHeader.from_bytes(data))

    def get_header(self) -> PhotonDataPacketHeader:
        return self._header

    def resize(self, new_data_length: int) -> None:
        self._data_length = new_data_length

    def serialize(self) -> bytes:
        return super().serialize() + self._header.serialize(self._data_length)

    def log(self) -> list[str]:
        length = (
            self._data_length
            if self._data_length is not None
            else self.get_header().packet_length
        )
        return [f"{self.get_header().get_command_name()}, Length: {length}"]
