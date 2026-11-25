"""Modbus constants: function codes, exception codes and request limits.

References: MODBUS Application Protocol Specification V1.1b3.
"""
from __future__ import annotations

from enum import IntEnum


class FunctionCode(IntEnum):
    READ_COILS = 0x01
    READ_DISCRETE_INPUTS = 0x02
    READ_HOLDING_REGISTERS = 0x03
    READ_INPUT_REGISTERS = 0x04
    WRITE_SINGLE_COIL = 0x05
    WRITE_SINGLE_REGISTER = 0x06
    WRITE_MULTIPLE_COILS = 0x0F
    WRITE_MULTIPLE_REGISTERS = 0x10


class ExceptionCode(IntEnum):
    ILLEGAL_FUNCTION = 0x01
    ILLEGAL_DATA_ADDRESS = 0x02
    ILLEGAL_DATA_VALUE = 0x03
    SERVER_DEVICE_FAILURE = 0x04
    ACKNOWLEDGE = 0x05
    SERVER_DEVICE_BUSY = 0x06
    # 0x07 is intentionally absent: not listed in Application Protocol V1.1b3 section 7.
    MEMORY_PARITY_ERROR = 0x08
    GATEWAY_PATH_UNAVAILABLE = 0x0A
    GATEWAY_TARGET_NO_RESPONSE = 0x0B


SUPPORTED_FUNCTION_CODES = frozenset(int(fc) for fc in FunctionCode)
WRITE_FUNCTION_CODES = frozenset({0x05, 0x06, 0x0F, 0x10})

# Quantity limits (Application Protocol V1.1b3, section 6).
MAX_READ_BITS = 2000          # FC01 / FC02: 0x0001..0x07D0
MAX_READ_REGISTERS = 125      # FC03 / FC04: 0x0001..0x007D
MAX_WRITE_COILS = 1968        # FC0F: 0x0001..0x07B0
MAX_WRITE_REGISTERS = 123     # FC10: 0x0001..0x007B

COIL_ON = 0xFF00
COIL_OFF = 0x0000

ADDRESS_SPACE = 0x10000       # 16-bit protocol address space

# Table names used by the store.
TABLE_COIL = "coil"
TABLE_DISCRETE = "discrete"
TABLE_INPUT = "input"
TABLE_HOLDING = "holding"
BIT_TABLES = frozenset({TABLE_COIL, TABLE_DISCRETE})
REGISTER_TABLES = frozenset({TABLE_INPUT, TABLE_HOLDING})
TABLES = BIT_TABLES | REGISTER_TABLES
WRITABLE_TABLES = frozenset({TABLE_COIL, TABLE_HOLDING})

# MBAP
MBAP_HEADER_LEN = 7           # transaction(2) protocol(2) length(2) unit(1)
MBAP_MIN_LENGTH = 2           # unit id + function code
MBAP_MAX_LENGTH = 254         # unit id + max PDU (253)
MAX_PDU_LEN = 253
