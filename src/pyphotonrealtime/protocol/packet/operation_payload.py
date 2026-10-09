from __future__ import annotations

from typing import TYPE_CHECKING, Any

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from pyphotonrealtime.protocol.deserializer import deserialize_photon_payload
from pyphotonrealtime.protocol.enum_lookups import get_operation_name
from pyphotonrealtime.protocol.param.nil_param import NilParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol
from pyphotonrealtime.protocol.serializer import serialize_photon_payload

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.enum_lookups import CommandParams
    from pyphotonrealtime.protocol.operation_code import OperationCode
    from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader
    from pyphotonrealtime.protocol.param.base import ParameterBase


def _decrypt_data(encrypted_data: bytes, aes_key: bytes) -> bytes:
    iv = bytes(16)
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    padded_plaintext = cipher.decrypt(encrypted_data)
    return unpad(padded_plaintext, AES.block_size)


def _encrypt_data(plaintext: bytes, aes_key: bytes) -> bytes:
    iv = bytes(16)
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    padded_plaintext = pad(plaintext, AES.block_size)
    return cipher.encrypt(padded_plaintext)


class PhotonPacketPayload:
    params: CommandParams
    operation_code: OperationCode
    response_debug_data: tuple[int, ParameterBase[Any]] | None
    header: PhotonDataPacketHeader

    def __init__(
        self,
        operation_code: OperationCode,
        params: CommandParams,
        header: PhotonDataPacketHeader,
        response_debug_data: tuple[int, ParameterBase[Any]] | None = None,
        protocol: SerializationProtocol = SerializationProtocol.V16,
    ) -> None:
        self.operation_code = operation_code
        self.params = params
        self.response_debug_data = response_debug_data
        self.header = header
        self.protocol = protocol
        self._cached_serialized: bytes | None = None

    @staticmethod
    def from_bytes(
        header: PhotonDataPacketHeader,
        data: bytes,
        protocol: SerializationProtocol = SerializationProtocol.V16,
    ) -> PhotonPacketPayload:
        operation_code, params, response_debug_data = deserialize_photon_payload(
            header, data, protocol
        )
        return PhotonPacketPayload(
            operation_code=operation_code,
            params=params,
            response_debug_data=response_debug_data,
            header=header,
            protocol=protocol,
        )

    def serialize(self) -> bytes:
        if self._cached_serialized is not None:
            return self._cached_serialized

        serialized_data = serialize_photon_payload(
            self.operation_code,
            self.params,
            self.response_debug_data,
            header=self.header,
            protocol=self.protocol,
        )
        self._cached_serialized = serialized_data
        return serialized_data

    def get_operation_name(self) -> str:
        return get_operation_name(self.operation_code)

    def get_return_code(self) -> int | None:
        return (
            self.response_debug_data[0]
            if self.response_debug_data is not None
            else None
        )

    def get_debug_message(self) -> str | None:
        if self.response_debug_data is None or isinstance(
            self.response_debug_data[1], NilParameter
        ):
            return None
        if not isinstance(self.response_debug_data[1], StringParameter):
            msg = "Debug message is not a string!"
            raise TypeError(msg)
        return self.response_debug_data[1].value


class PhotonPacketEncryptedPayload:
    raw: bytes
    is_response: bool
    header: PhotonDataPacketHeader

    @staticmethod
    def from_bytes(
        header: PhotonDataPacketHeader, data: bytes
    ) -> PhotonPacketEncryptedPayload:
        packet = PhotonPacketEncryptedPayload()
        packet.raw = data
        packet.header = header
        return packet

    def decrypt(
        self,
        aes_key: bytes,
        protocol: SerializationProtocol = SerializationProtocol.V16,
    ) -> PhotonPacketPayload:
        plaintext = _decrypt_data(self.raw, aes_key)
        return PhotonPacketPayload.from_bytes(
            header=self.header, data=plaintext, protocol=protocol
        )

    @staticmethod
    def encrypt(
        payload: PhotonPacketPayload,
        aes_key: bytes,
    ) -> bytes:
        return _encrypt_data(
            payload.serialize(),
            aes_key,
        )
