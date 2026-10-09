from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.packet.base import PhotonDataPacket
from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.packet.operation_payload import PhotonPacketPayload
from pyphotonrealtime.protocol.param.nil_param import NilParameter
from pyphotonrealtime.protocol.param.string_param import StringParameter
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.enum_lookups import CommandParams
    from pyphotonrealtime.protocol.event_code import EventCode
    from pyphotonrealtime.protocol.operation_code import OperationCode
    from pyphotonrealtime.protocol.packet.event_packet import PhotonEventPacket


class PacketFactory:
    @staticmethod
    def data(command_code: CommandCode) -> PhotonDataPacket:
        header = PhotonDataPacketHeader(command_code=command_code)
        return PhotonDataPacket(header)

    @staticmethod
    def operation(  # noqa: PLR0913 - mirrors the wire fields
        command: (
            Literal[
                CommandCode.Event,
                CommandCode.EncryptedEvent,
                CommandCode.Operation,
                CommandCode.OperationResponse,
                CommandCode.EncryptedOperation,
                CommandCode.EncryptedOperationResponse,
            ]
        ),
        operation: OperationCode,
        params: CommandParams | None = None,
        return_code: int | None = None,
        error_message: str | None = None,
        *,
        protocol: SerializationProtocol = SerializationProtocol.V16,
    ) -> PhotonOperationPacket:
        if params is None:
            params = {}
        response_debug_data = None
        if return_code is not None:
            response_debug_data = (
                return_code,
                (
                    StringParameter(error_message)
                    if error_message is not None
                    else NilParameter()
                ),
            )
        header = PhotonDataPacketHeader(command_code=command)
        return PhotonOperationPacket(
            header=header,
            payload=PhotonPacketPayload(
                operation_code=operation,
                params=params,
                header=header,
                response_debug_data=response_debug_data,
                protocol=protocol,
            ),
        )

    @staticmethod
    def event(  # noqa: PLR0913 - mirrors the wire fields
        event: EventCode,
        params: CommandParams | None = None,
        return_code: int | None = None,
        error_message: str | None = None,
        *,
        encrypted: bool = False,
        protocol: SerializationProtocol = SerializationProtocol.V16,
    ) -> PhotonEventPacket:
        return cast(
            "PhotonEventPacket",
            PacketFactory.operation(
                CommandCode.EncryptedEvent if encrypted else CommandCode.Event,
                operation=cast("OperationCode", event),
                params=params,
                return_code=return_code,
                error_message=error_message,
                protocol=protocol,
            ),
        )

    @staticmethod
    def encrypted_event(
        event: EventCode,
        params: CommandParams | None = None,
        return_code: int | None = None,
        error_message: str | None = None,
    ) -> PhotonEventPacket:
        return PacketFactory.event(
            event=event,
            params=params,
            return_code=return_code,
            error_message=error_message,
            encrypted=True,
        )
