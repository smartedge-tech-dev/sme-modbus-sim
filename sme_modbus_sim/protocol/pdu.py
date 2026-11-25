"""PDU decoding, dispatch and response encoding for the supported function codes.

Check order follows the server state diagrams of Application Protocol V1.1b3 section 6:
function code supported (01) -> quantity / byte count / value (03) -> address (02)
-> execution (04 on unexpected failure).
"""
from __future__ import annotations

import struct

from .constants import (
    COIL_OFF,
    COIL_ON,
    MAX_READ_BITS,
    MAX_READ_REGISTERS,
    MAX_WRITE_COILS,
    MAX_WRITE_REGISTERS,
    SUPPORTED_FUNCTION_CODES,
    TABLE_COIL,
    TABLE_DISCRETE,
    TABLE_HOLDING,
    TABLE_INPUT,
    WRITE_FUNCTION_CODES,
    ExceptionCode,
)
from .errors import ModbusError
from .store import UnitStore

_READ_TABLES = {0x01: TABLE_COIL, 0x02: TABLE_DISCRETE, 0x03: TABLE_HOLDING, 0x04: TABLE_INPUT}
_NAMES = {
    0x01: "read coils",
    0x02: "read discrete",
    0x03: "read holding",
    0x04: "read input",
    0x05: "write coil",
    0x06: "write register",
    0x0F: "write coils",
    0x10: "write registers",
}


def exception_response(function_code: int, exception_code: int) -> bytes:
    return bytes([(function_code | 0x80) & 0xFF, exception_code & 0xFF])


def pack_bits(bits: list[int]) -> bytes:
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        if b:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def unpack_bits(data: bytes, count: int) -> list[int]:
    return [(data[i // 8] >> (i % 8)) & 1 for i in range(count)]


def is_write(function_code: int) -> bool:
    return function_code in WRITE_FUNCTION_CODES


def _value_error(msg: str) -> ModbusError:
    return ModbusError(ExceptionCode.ILLEGAL_DATA_VALUE, msg)


def _header(pdu: bytes) -> tuple[int, int]:
    if len(pdu) < 5:
        raise _value_error("PDU too short")
    return struct.unpack(">HH", pdu[1:5])


def _process(store: UnitStore, pdu: bytes) -> bytes:
    fc = pdu[0]
    if fc in (0x01, 0x02):
        address, qty = _header(pdu)
        if len(pdu) != 5 or not 1 <= qty <= MAX_READ_BITS:
            raise _value_error(f"quantity {qty} not in 1..{MAX_READ_BITS}")
        data = pack_bits(store.read(_READ_TABLES[fc], address, qty))
        return bytes([fc, len(data)]) + data
    if fc in (0x03, 0x04):
        address, qty = _header(pdu)
        if len(pdu) != 5 or not 1 <= qty <= MAX_READ_REGISTERS:
            raise _value_error(f"quantity {qty} not in 1..{MAX_READ_REGISTERS}")
        regs = store.read(_READ_TABLES[fc], address, qty)
        return bytes([fc, 2 * qty]) + struct.pack(f">{qty}H", *regs)
    if fc == 0x05:
        address, value = _header(pdu)
        if len(pdu) != 5 or value not in (COIL_ON, COIL_OFF):
            raise _value_error(f"coil value 0x{value:04X} not 0xFF00/0x0000")
        store.write(TABLE_COIL, address, [1 if value == COIL_ON else 0])
        return bytes(pdu)
    if fc == 0x06:
        address, value = _header(pdu)
        if len(pdu) != 5:
            raise _value_error("bad PDU length")
        store.write(TABLE_HOLDING, address, [value])
        return bytes(pdu)
    if fc == 0x0F:
        address, qty = _header(pdu)
        if len(pdu) < 6:
            raise _value_error("missing byte count")
        byte_count = pdu[5]
        if not 1 <= qty <= MAX_WRITE_COILS or byte_count != (qty + 7) // 8 or len(pdu) != 6 + byte_count:
            raise _value_error(f"quantity {qty} / byte count {byte_count} invalid")
        store.write(TABLE_COIL, address, unpack_bits(pdu[6:], qty))
        return bytes(pdu[:5])
    if fc == 0x10:
        address, qty = _header(pdu)
        if len(pdu) < 6:
            raise _value_error("missing byte count")
        byte_count = pdu[5]
        if not 1 <= qty <= MAX_WRITE_REGISTERS or byte_count != 2 * qty or len(pdu) != 6 + byte_count:
            raise _value_error(f"quantity {qty} / byte count {byte_count} invalid")
        store.write(TABLE_HOLDING, address, list(struct.unpack(f">{qty}H", pdu[6:])))
        return bytes(pdu[:5])
    raise ModbusError(ExceptionCode.ILLEGAL_FUNCTION)  # pragma: no cover


def handle_pdu(store: UnitStore, pdu: bytes) -> bytes:
    """Execute a request PDU against ``store`` and return the response PDU."""
    if not pdu:
        raise ValueError("empty PDU")
    fc = pdu[0]
    if fc not in SUPPORTED_FUNCTION_CODES:
        return exception_response(fc, ExceptionCode.ILLEGAL_FUNCTION)
    try:
        return _process(store, pdu)
    except ModbusError as exc:
        return exception_response(fc, exc.exception_code)
    except Exception:  # noqa: BLE001 - any unexpected failure maps to 04
        return exception_response(fc, ExceptionCode.SERVER_DEVICE_FAILURE)


def describe_request(pdu: bytes) -> str:
    """Short human readable summary of a request PDU (for traffic trace)."""
    if not pdu:
        return "empty"
    fc = pdu[0]
    name = _NAMES.get(fc, "unsupported")
    if len(pdu) >= 5 and fc in _NAMES:
        a, b = struct.unpack(">HH", pdu[1:5])
        if fc in (0x05, 0x06):
            return f"FC{fc:02X} {name} addr={a} value=0x{b:04X}"
        return f"FC{fc:02X} {name} addr={a} qty={b}"
    return f"FC{fc:02X} {name} len={len(pdu)}"


def describe_response(pdu: bytes) -> tuple[str, int | None]:
    """Summary of a response PDU plus its exception code (or None)."""
    if not pdu:
        return "empty", None
    fc = pdu[0]
    if fc & 0x80 and len(pdu) >= 2:
        return f"FC{fc & 0x7F:02X} exception {pdu[1]:02X}", pdu[1]
    if fc in _READ_TABLES and len(pdu) >= 2:
        return f"FC{fc:02X} ok {pdu[1]} bytes", None
    return f"FC{fc:02X} ok", None
