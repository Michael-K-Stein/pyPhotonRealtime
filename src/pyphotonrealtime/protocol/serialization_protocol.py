from enum import IntEnum


class SerializationProtocol(IntEnum):
    """Serialization protocol, as announced in the init request (1.x -> x)."""

    V6 = 6
    V7 = 7
    V8 = 8

    V16 = 6
    """Protocol 1.6 (``GpBinaryV16``), the pre-v4.1.6 default."""
    V18 = 8
    """Protocol 1.8 (``GpBinaryV18``), the default of current SDKs."""
