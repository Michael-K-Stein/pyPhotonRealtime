"""Custom types: your own classes as Photon values (``PhotonPeer.RegisterType``).

Register a class with a one-byte code and a pair of functions that turn an
instance into bytes and back. Afterwards instances can go anywhere a plain
value can (event content, properties, operation parameters) and arrive as
instances again::

    @dataclass
    class Vector2:
        x: float
        y: float


    register_type(
        Vector2,
        code=ord("W"),
        serialize=lambda v: struct.pack(">ff", v.x, v.y),
        deserialize=lambda data: Vector2(*struct.unpack(">ff", data)),
    )

Every client in a room must register the same code for the same type; the
server just passes the bytes through. Like the SDKs' ``RegisterType``, the
registry is process-wide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

_CODE_MAX = 0xFF


@dataclass(frozen=True, slots=True)
class CustomType[T]:
    """A registered type: its class, wire code and (de)serializers."""

    cls: type[T]
    code: int
    serialize: Callable[[T], bytes]
    deserialize: Callable[[bytes], T]


_by_code: dict[int, CustomType[Any]] = {}
_by_class: dict[type, CustomType[Any]] = {}


def register_type[T](
    cls: type[T],
    code: int,
    serialize: Callable[[T], bytes],
    deserialize: Callable[[bytes], T],
) -> None:
    """Make ``cls`` serializable as custom type ``code``.

    Codes below 100 are sent one byte shorter with Protocol 1.8.

    Raises:
        ValueError: ``code`` is not a byte, or ``cls`` or ``code`` is already
            registered (unregister it first).
    """
    if not 0 <= code <= _CODE_MAX:
        msg = f"custom type code must be 0-255, got {code}"
        raise ValueError(msg)
    if code in _by_code:
        msg = f"custom type code {code} is taken by {_by_code[code].cls.__name__}"
        raise ValueError(msg)
    if cls in _by_class:
        msg = f"{cls.__name__} is already registered as code {_by_class[cls].code}"
        raise ValueError(msg)
    custom = CustomType(cls, code, serialize, deserialize)
    _by_code[code] = custom
    _by_class[cls] = custom


def unregister_type(cls: type) -> bool:
    """Forget a registered type.

    Returns:
        Whether ``cls`` was registered.
    """
    custom = _by_class.pop(cls, None)
    if custom is None:
        return False
    del _by_code[custom.code]
    return True


def custom_type_for_code(code: int) -> CustomType[Any] | None:
    """The type registered as ``code``, if any."""
    return _by_code.get(code)


def custom_type_for_value(value: object) -> CustomType[Any] | None:
    """The registered type ``value`` is an instance of, if any."""
    for cls in type(value).__mro__:
        if (custom := _by_class.get(cls)) is not None:
            return custom
    return None
