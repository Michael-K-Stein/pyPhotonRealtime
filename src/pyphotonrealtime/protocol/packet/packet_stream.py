from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from pyphotonrealtime.protocol.command_code import CommandCode, InternalOperationCode
from pyphotonrealtime.protocol.packet.base import PhotonPacket
from pyphotonrealtime.protocol.packet.disconnect import DisconnectMessagePacket
from pyphotonrealtime.protocol.packet.format import PacketFormat
from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader
from pyphotonrealtime.protocol.packet.init import InitRequestPacket, InitResponsePacket
from pyphotonrealtime.protocol.packet.keep_alive import (
    PhotonKeepAliveRequest,
    PhotonKeepAliveResponse,
)
from pyphotonrealtime.protocol.packet.key_exchange import (
    InitEncryptionRequest,
    InitEncryptionResponse,
)
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol

if TYPE_CHECKING:
    from collections.abc import Generator

    from pyphotonrealtime.protocol.packet.base import PhotonDataPacket

# Format byte + 4-byte client time (+ 4-byte server uptime in responses).
KEEP_ALIVE_REQUEST_SIZE = 5
KEEP_ALIVE_RESPONSE_SIZE = 9


class PhotonStreamParser:
    # Large maps can produce very large event payloads; keep a safety cap but avoid
    # disconnecting valid clients because of overly strict packet limits.
    MAX_PACKET_LENGTH = 16 * 1024 * 1024

    def __init__(
        self, protocol: SerializationProtocol = SerializationProtocol.V16
    ) -> None:
        # This persistent buffer survives across multiple socket.recv() calls
        self.buffer = bytearray()
        self.protocol = protocol
        """Serialization of operation payloads; follows a parsed init request."""

    def feed(self, new_data: bytes) -> None:
        self.buffer.extend(new_data)

    def parse(
        self,
        *,
        expect_responses: bool = False,
        aes_key: bytes | None = None,
    ) -> Generator[PhotonPacket]:
        """Yield every complete packet in the buffer, consuming its bytes."""
        datastream = BytesIO(self.buffer)

        while datastream.tell() < len(self.buffer):
            start_pos = datastream.tell()
            photon_packet = PhotonPacket.from_bytes(datastream)
            available = len(self.buffer) - start_pos

            parsed_packet: PhotonPacket | None
            if photon_packet.get_format() == PacketFormat.KeepAlive:
                parsed_packet = self._parse_keep_alive(
                    datastream, available, expect_responses=expect_responses
                )
            elif photon_packet.get_format() == PacketFormat.Data:
                parsed_packet = self._parse_data(datastream, available, aes_key)
            else:
                msg = "Unexpected packet format"
                raise ValueError(msg)

            if parsed_packet is None:
                break  # Incomplete packet; wait for more data.

            # Slice off the bytes we just successfully parsed from the front of
            # the buffer BEFORE yielding. Consumers that don't exhaust this
            # generator (e.g. `for packet in parse(): return packet`) would
            # otherwise see the same packet again on the next call.
            bytes_consumed = datastream.tell() - start_pos
            del self.buffer[:bytes_consumed]

            # Reset datastream to point to the new beginning of the buffer
            datastream = BytesIO(self.buffer)

            yield parsed_packet

    @staticmethod
    def _parse_keep_alive(
        datastream: BytesIO, available: int, *, expect_responses: bool
    ) -> PhotonPacket | None:
        if expect_responses:
            if available < KEEP_ALIVE_RESPONSE_SIZE:
                return None
            return PhotonKeepAliveResponse.from_bytes(datastream)
        if available < KEEP_ALIVE_REQUEST_SIZE:
            return None
        return PhotonKeepAliveRequest.from_bytes(datastream)

    def _parse_data(
        self, datastream: BytesIO, available: int, aes_key: bytes | None
    ) -> PhotonPacket | None:
        # We need to peek/parse the header to know the required length
        if available < PhotonDataPacketHeader.size():
            return None
        data_header = PhotonDataPacketHeader.from_bytes(datastream)
        if data_header.packet_length is None:
            msg = "Packet length is missing"
            raise ValueError(msg)
        if data_header.packet_length <= 0:
            msg = "Invalid packet length"
            raise ValueError(msg)
        if data_header.packet_length > self.MAX_PACKET_LENGTH:
            msg = f"Packet too large: {data_header.packet_length} bytes"
            raise ValueError(msg)
        if available < data_header.packet_length:
            return None

        content_data = datastream.read(data_header.packet_length - data_header.size())
        return self._handle_data_packet(data_header, content_data, aes_key)

    def _handle_data_packet(
        self,
        header: PhotonDataPacketHeader,
        data: bytes,
        aes_key: bytes | None = None,
    ) -> PhotonDataPacket:
        datastream = BytesIO(data)
        if header.is_operation():
            return self._handle_operation_packet(header, datastream, aes_key)

        if header.get_command_code() == CommandCode.Init:
            return self._handle_data_init_packet(header, datastream)
        if header.get_command_code() == CommandCode.InitResponse:
            return self._handle_data_init_response_packet(header, datastream)

        msg = f"Unhandled command: {header.get_command_name()}"
        raise ValueError(msg)

    def _handle_data_init_packet(
        self, header: PhotonDataPacketHeader, data: BytesIO
    ) -> InitRequestPacket:
        packet = InitRequestPacket.from_bytes(data, header=header)
        # Server side: the client picks the protocol for the whole connection.
        self.protocol = packet.serialization_protocol
        return packet

    def _handle_data_init_response_packet(
        self, header: PhotonDataPacketHeader, data: BytesIO
    ) -> InitResponsePacket:
        return InitResponsePacket.from_bytes(data, header=header)

    def _handle_operation_packet(
        self,
        header: PhotonDataPacketHeader,
        data: BytesIO,
        aes_key: bytes | None = None,
    ) -> PhotonOperationPacket:
        if header.get_command_code() == CommandCode.DisconnectMessage:
            return DisconnectMessagePacket.from_bytes(
                data, header=header, aes_key=aes_key, protocol=self.protocol
            )

        packet = PhotonOperationPacket.from_bytes(
            data, header=header, aes_key=aes_key, protocol=self.protocol
        )
        # Internal operations: the key exchange (code 0) or a ping (code 1).
        if (
            int(packet.get_payload().operation_code)
            == InternalOperationCode.InitEncryption
        ):
            if header.get_command_code() == CommandCode.KeyExchangeRequest:
                return InitEncryptionRequest.from_operation(packet)
            if header.get_command_code() == CommandCode.KeyExchangeResponse:
                return InitEncryptionResponse.from_operation(packet)
        return packet
