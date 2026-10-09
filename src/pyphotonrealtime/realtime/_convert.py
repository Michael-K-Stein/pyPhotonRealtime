"""Unwrap protocol ``*Parameter`` objects into plain Python values."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyphotonrealtime.protocol.param.base import ArrayParameterBase
from pyphotonrealtime.protocol.param.dictionary_param_base import (
    DictionaryParameterBase,
)

if TYPE_CHECKING:
    from pyphotonrealtime.protocol.param.base import ParameterBase


def to_python(param: ParameterBase[Any]) -> Any:  # noqa: ANN401
    """Recursively convert a parameter into ``dict`` / ``list`` / scalars.

    Returns:
        The plain value.
    """
    if isinstance(param, DictionaryParameterBase):
        return {to_python(k): to_python(v) for k, v in param.items()}
    if isinstance(param, ArrayParameterBase):
        return [to_python(element) for element in param]
    return param.value
