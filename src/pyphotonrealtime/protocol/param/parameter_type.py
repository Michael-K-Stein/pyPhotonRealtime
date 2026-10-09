from __future__ import annotations

from enum import IntEnum


class ParameterType(IntEnum):
    NilType = 42
    DictionaryType = 68
    StringSliceType = 97
    Int8Type = 98
    Custom = 99
    DoubleType = 100
    EventDateType = 101
    Float32Type = 102
    Hashtable = 104
    Int32Type = 105
    Int16Type = 107
    Int64Type = 108
    Int32SliceType = 110
    BooleanType = 111
    OperationResponseType = 112
    OperationRequestType = 113
    StringType = 115
    Int8SliceType = 120
    SliceType = 121
    ObjectSliceType = 122

    @classmethod
    def _missing_(cls, value: object) -> ParameterType:
        if not isinstance(value, int):
            msg = "Value must be an int"
            raise TypeError(msg)

        # Protocol 1.6 legacy codes that alias newer ones.
        legacy = {0: cls.NilType, 7: cls.Int16Type}
        if value in legacy:
            return legacy[value]

        msg = f"Not a valid id: {value}"
        raise ValueError(msg)
