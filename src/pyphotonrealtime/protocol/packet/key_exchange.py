from __future__ import annotations

from typing import TYPE_CHECKING, Self

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.packet.operation_payload import PhotonPacketPayload
from pyphotonrealtime.protocol.param.int8_slice_param import Int8SliceParameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey

if TYPE_CHECKING:
    from io import BytesIO


def _extract_public_key(payload: PhotonPacketPayload) -> bytes:
    pub_key = payload.params.get(ParameterKey.ClientKey)
    if not isinstance(pub_key, Int8SliceParameter):
        msg = "No public key found!"
        raise TypeError(msg)
    return pub_key.value


class KeyExchangePacket(PhotonOperationPacket):
    _public_key: bytes

    def __init__(
        self,
        *,
        header: PhotonDataPacketHeader,
        public_key: Int8SliceParameter | bytes | None = None,
        payload: PhotonPacketPayload | None = None,
        aes_key: bytes | None = None,  # noqa: ARG002 - key exchange is never encrypted
    ) -> None:
        client_key = (
            Int8SliceParameter(public_key)
            if isinstance(public_key, bytes)
            else public_key
        )

        if payload is None:
            if client_key is None:
                msg = "Payload and public key cannot both be None!"
                raise ValueError(msg)
            payload = PhotonPacketPayload(
                operation_code=(
                    OperationCode.DiffieHellmanRequest
                    if header.command == CommandCode.KeyExchangeRequest
                    else OperationCode.DiffieHellmanResponse
                ),
                params={ParameterKey.ClientKey: client_key},
                header=header,
            )
        elif client_key is not None:
            payload.params[ParameterKey.ClientKey] = client_key

        super().__init__(header=header, payload=payload)
        self._public_key = (
            client_key.value if client_key is not None else _extract_public_key(payload)
        )

    @classmethod
    def from_bytes(
        cls,
        data: BytesIO,
        *,
        header: PhotonDataPacketHeader | None = None,
        aes_key: bytes | None = None,  # noqa: ARG003 - key exchange is never encrypted
    ) -> Self:
        packet = PhotonOperationPacket.from_bytes(data, header=header, aes_key=None)
        return cls(
            header=packet.get_header(),
            public_key=_extract_public_key(packet.get_payload()),
        )

    def get_public_key(self) -> bytes:
        return self._public_key

    def get_public_key_int(self) -> int:
        return int.from_bytes(self._public_key, byteorder="big", signed=False)


class InitEncryptionRequest(KeyExchangePacket):
    def __init__(
        self,
        *,
        public_key: Int8SliceParameter | bytes | None = None,
        header: PhotonDataPacketHeader | None = None,
        payload: PhotonPacketPayload | None = None,
        aes_key: bytes | None = None,
    ) -> None:
        super().__init__(
            header=(
                header
                if header is not None
                else PhotonDataPacketHeader(command_code=CommandCode.KeyExchangeRequest)
            ),
            public_key=public_key,
            payload=payload,
            aes_key=aes_key,
        )


class InitEncryptionResponse(KeyExchangePacket):
    def __init__(
        self,
        *,
        public_key: Int8SliceParameter | bytes | None = None,
        header: PhotonDataPacketHeader | None = None,
        payload: PhotonPacketPayload | None = None,
        aes_key: bytes | None = None,
    ) -> None:
        super().__init__(
            header=(
                header
                if header is not None
                else PhotonDataPacketHeader(
                    command_code=CommandCode.KeyExchangeResponse
                )
            ),
            public_key=public_key,
            payload=payload,
            aes_key=aes_key,
        )
