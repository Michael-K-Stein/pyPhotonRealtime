from typing import Literal, Optional, cast

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.enum_lookups import CommandParams
from pyphotonrealtime.protocol.event_code import EventCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.packet.base import PhotonDataPacket
from pyphotonrealtime.protocol.packet.event_packet import PhotonEventPacket
from pyphotonrealtime.protocol.packet.header import PhotonDataPacketHeader
from pyphotonrealtime.protocol.packet.operation_packet import PhotonOperationPacket
from pyphotonrealtime.protocol.packet.operation_payload import PhotonPacketPayload
from pyphotonrealtime.protocol.param.nil_param import NilParameter
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.string_param import StringParameter

class PacketFactory:
    @staticmethod
    def data(command_code: CommandCode) -> PhotonDataPacket:
        header = PhotonDataPacketHeader(command_code=command_code)
        return PhotonDataPacket(header)

    @staticmethod
    def operation(
        command: (
            Literal[CommandCode.Event]
            | Literal[CommandCode.EncryptedEvent]
            | Literal[CommandCode.Operation]
            | Literal[CommandCode.OperationResponse]
            | Literal[CommandCode.EncryptedOperation]
            | Literal[CommandCode.EncryptedOperationResponse]
        ),
        operation: OperationCode,
        params: Optional[CommandParams] = None,
        return_code: Optional[int] = None,
        error_message: Optional[str] = None,
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
            ),
        )

    @staticmethod
    def event(
        event: EventCode,
        params: Optional[CommandParams] = None,
        return_code: Optional[int] = None,
        error_message: Optional[str] = None,
        encrypted: bool = False,
    ) -> PhotonEventPacket:
        return cast(
            PhotonEventPacket,
            PacketFactory.operation(
                CommandCode.EncryptedEvent if encrypted else CommandCode.Event,
                operation=cast(OperationCode, event),
                params=params,
                return_code=return_code,
                error_message=error_message,
            ),
        )

    @staticmethod
    def encrypted_event(
        event: EventCode,
        params: Optional[CommandParams] = None,
        return_code: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> PhotonEventPacket:
        return PacketFactory.event(
            event=event,
            params=params,
            return_code=return_code,
            error_message=error_message,
            encrypted=True,
        )
