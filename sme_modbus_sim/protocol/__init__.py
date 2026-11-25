"""Pure-stdlib Modbus protocol core (no FastAPI / pydantic imports)."""
from __future__ import annotations

from .constants import ExceptionCode, FunctionCode
from .datatypes import decode, encode, get_bit, register_count, set_bit
from .errors import ModbusError
from .framer import DroppedFrame, Frame, MbapFramer, RtuFramer, crc16, crc_bytes
from .pdu import handle_pdu
from .server import ModbusTcpServer, TraceEvent
from .store import UnitStore

__all__ = [
    "DroppedFrame",
    "ExceptionCode",
    "Frame",
    "FunctionCode",
    "MbapFramer",
    "ModbusError",
    "ModbusTcpServer",
    "RtuFramer",
    "TraceEvent",
    "UnitStore",
    "crc16",
    "crc_bytes",
    "decode",
    "encode",
    "get_bit",
    "handle_pdu",
    "register_count",
    "set_bit",
]
