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
    DEVICE_ID_BASIC,
    DEVICE_ID_EXTENDED,
    DEVICE_ID_INDIVIDUAL,
    DEVICE_ID_REGULAR,
    MEI_READ_DEVICE_ID,
    MAX_READ_BITS,
    MAX_READ_REGISTERS,
    MAX_PDU_LEN,
    MAX_RW_WRITE_REGISTERS,
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
    0x16: "mask write register",
    0x17: "read/write registers",
    0x2B: "read device id",
}
DEVICE_ID_HEADER_LEN = 7  # fc mei code conformity more-follows next-id count
_MAX_OBJECT_LEN = MAX_PDU_LEN - DEVICE_ID_HEADER_LEN - 2


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
    if fc == 0x16:
        if len(pdu) != 7:
            raise _value_error("bad PDU length")
        address, and_mask, or_mask = struct.unpack(">HHH", pdu[1:7])
        current = store.read(TABLE_HOLDING, address, 1)[0]
        result = (current & and_mask) | (or_mask & ~and_mask & 0xFFFF)
        store.write(TABLE_HOLDING, address, [result])
        return bytes(pdu)
    if fc == 0x17:
        if len(pdu) < 10:
            raise _value_error("PDU too short")
        r_addr, r_qty, w_addr, w_qty, byte_count = struct.unpack(">HHHHB", pdu[1:10])
        if not 1 <= r_qty <= MAX_READ_REGISTERS or not 1 <= w_qty <= MAX_RW_WRITE_REGISTERS                 or byte_count != 2 * w_qty or len(pdu) != 10 + byte_count:
            raise _value_error(f"read qty {r_qty} / write qty {w_qty} / byte count {byte_count} invalid")
        store.read(TABLE_HOLDING, r_addr, r_qty)  # address check (02) before anything is written
        store.write(TABLE_HOLDING, w_addr, list(struct.unpack(f">{w_qty}H", pdu[10:])))  # write first
        regs = store.read(TABLE_HOLDING, r_addr, r_qty)
        return bytes([fc, 2 * r_qty]) + struct.pack(f">{r_qty}H", *regs)
    if fc == 0x2B:
        if len(pdu) < 2 or pdu[1] != MEI_READ_DEVICE_ID:
            # MEI 0x0D (CANopen) and others are not implemented
            raise ModbusError(ExceptionCode.ILLEGAL_FUNCTION, "unsupported MEI type")
        if len(pdu) != 4:
            raise _value_error("bad PDU length")
        return read_device_identification(store.identification, pdu[2], pdu[3])
    raise ModbusError(ExceptionCode.ILLEGAL_FUNCTION)  # pragma: no cover


def _encode_object(value: str | bytes) -> bytes:
    raw = value if isinstance(value, bytes) else str(value).encode("ascii", "replace")
    return raw[:_MAX_OBJECT_LEN]


def conformity_level(objects: dict[int, bytes | str]) -> int:
    """0x81 basic / 0x82 regular / 0x83 extended, all with individual access (0x80 bit)."""
    if any(i >= 0x80 for i in objects):
        return 0x83
    if any(0x03 <= i <= 0x7F for i in objects):
        return 0x82
    return 0x81


def read_device_identification(objects: dict[int, bytes | str], code: int, object_id: int) -> bytes:
    """FC2B / MEI 0x0E response PDU (Application Protocol V1.1b3 section 6.21).

    Stream access (01 basic / 02 regular / 03 extended) starts at ``object_id``; an id that is not
    part of the requested category restarts at object 0. Objects that do not fit in one PDU are
    announced with More Follows = 0xFF and Next Object Id. Individual access (04) returns one object
    or exception 02 when it does not exist. Any other read code -> exception 03.
    """
    if code not in (DEVICE_ID_BASIC, DEVICE_ID_REGULAR, DEVICE_ID_EXTENDED, DEVICE_ID_INDIVIDUAL):
        raise _value_error(f"invalid Read Device ID code {code:02X}")
    encoded = {i: _encode_object(v) for i, v in objects.items() if v is not None}
    level = conformity_level(encoded)
    more, next_id, body, count = 0x00, 0x00, bytearray(), 0
    if code == DEVICE_ID_INDIVIDUAL:
        if object_id not in encoded:
            raise ModbusError(ExceptionCode.ILLEGAL_DATA_ADDRESS, f"object {object_id:02X} not available")
        v = encoded[object_id]
        body += bytes([object_id, len(v)]) + v
        count = 1
    else:
        limit = {DEVICE_ID_BASIC: 0x02, DEVICE_ID_REGULAR: 0x7F, DEVICE_ID_EXTENDED: 0xFF}[code]
        ids = sorted(i for i in encoded if i <= limit)
        start = ids.index(object_id) if object_id in ids else 0
        for i in ids[start:]:
            entry = bytes([i, len(encoded[i])]) + encoded[i]
            if DEVICE_ID_HEADER_LEN + len(body) + len(entry) > MAX_PDU_LEN:
                more, next_id = 0xFF, i
                break
            body += entry
            count += 1
    return bytes([0x2B, MEI_READ_DEVICE_ID, code, level, more, next_id, count]) + bytes(body)


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
    if fc == 0x16 and len(pdu) >= 7:
        a, m_and, m_or = struct.unpack(">HHH", pdu[1:7])
        return f"FC16 {name} addr={a} and=0x{m_and:04X} or=0x{m_or:04X}"
    if fc == 0x17 and len(pdu) >= 9:
        ra, rq, wa, wq = struct.unpack(">HHHH", pdu[1:9])
        return f"FC17 {name} read addr={ra} qty={rq} write addr={wa} qty={wq}"
    if fc == 0x2B:
        if len(pdu) >= 4 and pdu[1] == MEI_READ_DEVICE_ID:
            return f"FC2B/0E {name} code={pdu[2]:02X} object=0x{pdu[3]:02X}"
        return f"FC2B MEI {pdu[1]:02X}" if len(pdu) >= 2 else f"FC2B len={len(pdu)}"
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
    if fc == 0x2B and len(pdu) >= 7 and pdu[1] == MEI_READ_DEVICE_ID:
        more = " more follows" if pdu[4] == 0xFF else ""
        return f"FC2B/0E ok {pdu[6]} objects{more}", None
    if (fc in _READ_TABLES or fc == 0x17) and len(pdu) >= 2:
        return f"FC{fc:02X} ok {pdu[1]} bytes", None
    return f"FC{fc:02X} ok", None
