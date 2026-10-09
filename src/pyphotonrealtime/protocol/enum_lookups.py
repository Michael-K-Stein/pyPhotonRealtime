from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from pyphotonrealtime.protocol.command_code import CommandCode
from pyphotonrealtime.protocol.event_code import EventCode
from pyphotonrealtime.protocol.operation_code import OperationCode
from pyphotonrealtime.protocol.param.base import ParameterBase
from pyphotonrealtime.protocol.param.parameter_key import ParameterKey
from pyphotonrealtime.protocol.param.parameter_type import ParameterType
from pyphotonrealtime.protocol.serialization_protocol import SerializationProtocol

if TYPE_CHECKING:
    from enum import IntEnum


def build_value_lookup(enum_cls: type[IntEnum]) -> dict[int, list[str]]:
    lookup: dict[int, list[str]] = defaultdict(list)

    for name, member in enum_cls.__members__.items():
        lookup[int(member.value)].append(name)

    return dict(lookup)


OPERATION_CODE_LOOKUP = build_value_lookup(OperationCode)
EVENT_CODE_LOOKUP = build_value_lookup(EventCode)
PARAMETER_KEY_LOOKUP = build_value_lookup(ParameterKey)
PARAMETER_TYPE_LOOKUP = build_value_lookup(ParameterType)
COMMAND_CODE_LOOKUP = build_value_lookup(CommandCode)
SERIALIZATION_PROTOCOL_LOOKUP = build_value_lookup(SerializationProtocol)

CommandParams = dict[ParameterKey, ParameterBase[Any]]


def _get_x_name[XT](lookup_table: dict[XT, list[str]], v: XT) -> str:
    n = lookup_table.get(v)
    if n is None:
        return f"UNKNOWN[{v!s}]"
    if len(n) == 1:
        return n[0]
    return "/".join(n)


def get_parameter_key_name(key: ParameterKey) -> str:
    return _get_x_name(PARAMETER_KEY_LOOKUP, key)


def get_parameter_type_name(param_type: ParameterType) -> str:
    return _get_x_name(PARAMETER_TYPE_LOOKUP, param_type)


def get_operation_name(operation_code: OperationCode) -> str:
    return _get_x_name(OPERATION_CODE_LOOKUP, operation_code)


def get_event_name(event_code: EventCode) -> str:
    return _get_x_name(EVENT_CODE_LOOKUP, event_code)


def get_command_name(command_code: CommandCode) -> str:
    return _get_x_name(COMMAND_CODE_LOOKUP, command_code)


def get_serialization_protocol_name(protocol: SerializationProtocol) -> str:
    return _get_x_name(SERIALIZATION_PROTOCOL_LOOKUP, protocol)
