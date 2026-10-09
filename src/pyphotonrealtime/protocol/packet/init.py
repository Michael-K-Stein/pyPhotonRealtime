from __future__ import annotations

from io import BytesIO
from struct import Struct, pack
from typing import TYPE_CHECKING, Any, Self, cast
from urllib.parse import parse_qs, urlsplit

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.packet.base import PhotonDataPacket
from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.param.base import ParameterBase

# App ids travel as 32 hex chars (a UUID without dashes).
APP_ID_LENGTH = 32
HTTP_INIT_PREFIX = b"PO"
"""Peer id, then ``POST /?init=...``: an init sent as an HTTP request."""
_PROTOCOL_NAMES = {
    "GpBinaryV16": SerializationProtocol.V16,
    "GpBinaryV18": SerializationProtocol.V18,
}


class InitRequestPacket(PhotonDataPacket):
    ip_protocol: int
    serialization_protocol: SerializationProtocol
    app_id: str
    sdk_id: int

    def __init__(  # noqa: PLR0913 - mirrors the wire fields
        self,
        app_id: str,
        *,
        ip_protocol: int = 1,
        serialization_protocol: SerializationProtocol = SerializationProtocol.V6,
        sdk_id: int = 17,
        sdk_version: str = "4.1.11.0",
        header: PhotonDataPacketHeader | None = None,
    ) -> None:
        if header is None:
            header = PhotonDataPacketHeader(command_code=CommandCode.Init)
        super().__init__(header)
        self.app_id = app_id
        self.ip_protocol = ip_protocol
        self.serialization_protocol = serialization_protocol
        self.sdk_version = sdk_version
        self.sdk_id = sdk_id
        self.custom_init_data: ParameterBase[Any] | None = None
        """Sent with an HTTP-style init, e.g. the AuthOnce token."""

    @classmethod
    def from_http(cls, request: bytes, *, header: PhotonDataPacketHeader) -> Self:
        """Parse the HTTP-style init that carries custom init data.

        The C++ SDK sends ``POST /?init=&app=...&protocol=GpBinaryV18&sid=17``
        with the custom data (the AuthOnce token) as one serialized value in
        the body, instead of the binary init.

        Returns:
            The init request.

        Raises:
            ValueError: Not a well-formed init request.
        """
        head, _, body = request.partition(b"\r\n\r\n")
        request_line = head.split(b"\r\n", 1)[0].decode("ascii", "replace")
        parts = request_line.split(" ")
        if len(parts) < 2 or parts[0] != "POST":  # noqa: PLR2004 -- method, path
            msg = f"Unexpected init request {request_line!r}"
            raise ValueError(msg)
        query = {k: v[0] for k, v in parse_qs(urlsplit(parts[1]).query).items()}
        protocol = _PROTOCOL_NAMES.get(query.get("protocol", ""))
        if protocol is None:
            msg = f"Unknown protocol in {request_line!r}"
            raise ValueError(msg)
        packet = cls(
            app_id=query.get("app", ""),
            serialization_protocol=protocol,
            sdk_id=int(query.get("sid", "0") or 0),
            sdk_version=query.get("clientversion", "0"),
            header=header,
        )
        if body:
            packet.custom_init_data = _read_value(BytesIO(body), protocol)
        return packet

    @classmethod
    def from_bytes(
        cls,
        data: BytesIO,
        *,
        header: PhotonDataPacketHeader | None = None,
    ) -> Self:
        if header is not None and header.get_command_code() != CommandCode.Init:
            msg = "Packet header is not of a init request!"
            raise TypeError(msg)

        # Byte 0: IP Protocol (1)
        # Byte 1: Serialization Protocol (6)
        # Byte 2: SDK ID (17)
        # Byte 3: Major/Minor Version
        # Byte 4: Patch Version
        # Bytes 5-6: Build Number
        header_struct = Struct(">BBBBBh")
        ip_protocol, serialization_version, sdk_id, major_minor, patch, build = (
            header_struct.unpack_from(data.read(header_struct.size))
        )

        major_version = major_minor >> 4
        minor_version = major_minor & 0x0F

        version_string = f"{major_version}.{minor_version}.{patch}.{build}"

        app_id = data.read(APP_ID_LENGTH)
        if len(app_id) != APP_ID_LENGTH:
            msg = "AppId is not 32 bytes!"
            raise ValueError(msg)

        return cls(
            app_id=app_id.decode(),
            sdk_id=sdk_id,
            sdk_version=version_string,
            serialization_protocol=cast("SerializationProtocol", serialization_version),
            ip_protocol=ip_protocol,
            header=header,
        )

    def serialize(self) -> bytes:
        # "major.minor.patch.build"; missing trailing components default to 0.
        parts = [int(part) for part in self.sdk_version.split(".")]
        major, minor, patch, build = [*parts, 0, 0, 0, 0][:4]
        major_minor = (major << 4) | (minor & 0x0F)

        payload_header = pack(
            ">BBBBBh",
            self.ip_protocol,
            int(self.serialization_protocol),
            self.sdk_id,
            major_minor,
            patch,
            build,
        )

        app_id_bytes = self.app_id.encode("utf-8")
        serialized_payload = payload_header + app_id_bytes

        super().resize(len(serialized_payload))
        return super().serialize() + serialized_payload


class InitResponsePacket(PhotonDataPacket):
    def __init__(self, header: PhotonDataPacketHeader | None = None) -> None:
        if header is None:
            header = PhotonDataPacketHeader(command_code=CommandCode.InitResponse)
        super().__init__(header)

    @classmethod
    def from_bytes(
        cls,
        data: BytesIO,
        *,
        header: PhotonDataPacketHeader | None = None,
    ) -> Self:
        if header is not None and header.get_command_code() != CommandCode.InitResponse:
            msg = "Packet header is not of a init response!"
            raise TypeError(msg)

        content = data.read(1)
        if content != b"\x00":
            msg = f"Unexpected data in InitResponse! Got {content!r}"
            raise ValueError(msg)

        return cls(header)

    def serialize(self) -> bytes:
        super().resize(1)
        return super().serialize() + b"\x00"


def _read_value(stream: BytesIO, protocol: SerializationProtocol) -> ParameterBase[Any]:
    # Imported here: the serializers import packet modules themselves.
    from pyphotonrealtime.protocol import protocol18  # noqa: PLC0415
    from pyphotonrealtime.protocol.param.read_param import (  # noqa: PLC0415
        read_parameter,
    )

    if protocol == SerializationProtocol.V18:
        return protocol18.read_value(stream)
    return read_parameter(stream)
