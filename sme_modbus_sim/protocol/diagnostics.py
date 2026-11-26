"""Serial line diagnostics: FC 07 / 08 / 0B / 0C / 11 and the counters behind them.

References: Application Protocol V1.1b3 sections 6.7-6.10 and 6.13 (APP), Serial Line V1.02
Appendix A, Counters Management Diagram (SER). These function codes are "Serial Line only":
the TCP servers answer them with exception 01 (``handle_pdu`` does not support them).

Counter scope (simulator choice):
  * bus counters, one set per serial listener (they describe the line):
    0B Bus Message (CPT1), 0C Bus Communication Error (CPT2), 12 Bus Character Overrun (CPT8).
  * server counters, one set per unit on that listener:
    0D Bus Exception Error (CPT3), 0E Server Message (CPT4), 0F Server No Response (CPT5),
    10 Server NAK (CPT6), 11 Server Busy (CPT7), plus the FC 0B event counter, the FC 0C event
    log and the Listen Only Mode flag.

Counting follows the SER diagram: a dropped frame (CRC / LRC error, too short, bad characters,
timeout) only counts CPT2; a correct frame counts CPT1 whatever its address; a frame for this
unit or broadcast counts CPT4; a broadcast counts CPT5; exceptions (also those detected in
broadcasts, which are never answered) count CPT3. The simulator never loses characters, so
CPT8 stays 0 (sub-function 14 still clears it).

Interpretations where the specs are silent or disagree (documented in docs/*/configuration.md):
  * sub-functions 05-09 -> exception 03; 13 and 15-FFFF -> exception 01 (not supported).
  * the diagnostic register (sub 02) is always 0.
  * sub 0A clears counters and the diagnostic register, not the event log.
  * sub 01 / 0A also clear the shared bus counters of the listener.
  * the Listen Only event byte is 04 (APP p.28 definition, not the 20 of the p.27 example).
  * counters and the event log keep counting in Listen Only Mode.
  * sub 03 only changes the delimiter the listener expects on ASCII requests (responses keep
    LF); restart (sub 01) restores LF. On an RTU listener sub 03 is echoed and has no effect.
  * the FC 0B event counter counts every message completed without exception (broadcasts
    included), except FC 0B and FC 0C themselves.
  * FC 07 returns 00; FC 11 returns the identification product_code as Server ID, run
    indicator FF, no additional data.
"""
from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass, field

from .constants import ExceptionCode
from .errors import ModbusError
from .framer import ASCII_CR, ASCII_START
from .pdu import exception_response

DIAGNOSTIC_FUNCTION_CODES = frozenset({0x07, 0x08, 0x0B, 0x0C, 0x11})
EVENT_LOG_DEPTH = 64
MAX_COUNTER = 0xFFFF

# event log bytes (APP section 6.10)
EVENT_RECEIVE = 0x80
EVENT_RECEIVE_BROADCAST = 0x40
EVENT_SEND = 0x40
EVENT_LISTEN_ONLY_BIT = 0x20
EVENT_ENTERED_LISTEN_ONLY = 0x04
EVENT_RESTART = 0x00

SUB_QUERY_DATA = 0x00
SUB_RESTART = 0x01
SUB_DIAG_REGISTER = 0x02
SUB_ASCII_DELIMITER = 0x03
SUB_LISTEN_ONLY = 0x04
SUB_CLEAR_COUNTERS = 0x0A
SUB_CLEAR_OVERRUN = 0x14
RESTART_CLEAR_LOG = 0xFF00
_RESERVED_03 = range(0x05, 0x0A)
_COUNTER_SUBS = {
    0x0B: "bus_message", 0x0C: "bus_comm_error", 0x0D: "exception", 0x0E: "server_message",
    0x0F: "no_response", 0x10: "nak", 0x11: "busy", 0x12: "bus_overrun",
}
_SUB_NAMES = {
    0x00: "return query data", 0x01: "restart communications", 0x02: "return diagnostic register",
    0x03: "change ASCII delimiter", 0x04: "force listen only", 0x0A: "clear counters",
    0x0B: "bus message count", 0x0C: "bus comm error count", 0x0D: "bus exception count",
    0x0E: "server message count", 0x0F: "server no response count", 0x10: "server NAK count",
    0x11: "server busy count", 0x12: "bus overrun count", 0x14: "clear overrun",
}


def inc(v: int) -> int:
    return (v + 1) & MAX_COUNTER


@dataclass
class BusCounters:
    bus_message: int = 0     # CPT1
    bus_comm_error: int = 0  # CPT2
    bus_overrun: int = 0     # CPT8

    def clear(self) -> None:
        self.bus_message = self.bus_comm_error = self.bus_overrun = 0


@dataclass
class UnitDiagnostics:
    exception: int = 0       # CPT3
    server_message: int = 0  # CPT4
    no_response: int = 0     # CPT5
    nak: int = 0             # CPT6
    busy: int = 0            # CPT7
    event_count: int = 0
    listen_only: bool = False
    events: deque = field(default_factory=lambda: deque(maxlen=EVENT_LOG_DEPTH))  # [0] = newest

    def clear_counters(self) -> None:
        self.exception = self.server_message = self.no_response = self.nak = self.busy = 0
        self.event_count = 0

    def log(self, event: int) -> None:
        self.events.appendleft(event & 0xFF)

    def receive_event(self, broadcast: bool) -> None:
        self.log(EVENT_RECEIVE | (EVENT_RECEIVE_BROADCAST if broadcast else 0)
                 | (EVENT_LISTEN_ONLY_BIT if self.listen_only else 0))

    def send_event(self, exception_code: int | None) -> None:
        ev = EVENT_SEND | (EVENT_LISTEN_ONLY_BIT if self.listen_only else 0)
        if exception_code is not None:
            if 1 <= exception_code <= 3:
                ev |= 0x01
            elif exception_code == 4:
                ev |= 0x02
            elif exception_code in (5, 6):
                ev |= 0x04
            elif exception_code == 7:
                ev |= 0x08
        self.log(ev)

    def account(self, fc: int, resp: bytes | None, broadcast: bool = False, completed: bool = True) -> None:
        """Count the outcome of a request (CPT3/5/6/7, event counter, send event).

        ``completed`` False: the request was not acted upon (Listen Only Mode, no_response fault).
        Broadcasts count CPT5 on reception, not here."""
        code = resp[1] if resp is not None and resp[0] & 0x80 and len(resp) >= 2 else None
        if resp is None and not broadcast:
            self.no_response = inc(self.no_response)
        if code is not None:
            self.exception = inc(self.exception)
            if code == 0x07:
                self.nak = inc(self.nak)
            elif code == ExceptionCode.SERVER_DEVICE_BUSY:
                self.busy = inc(self.busy)
        elif completed and fc not in (0x0B, 0x0C):
            self.event_count = inc(self.event_count)
        self.send_event(code)


@dataclass
class DiagResult:
    """Outcome of a diagnostic request. ``response`` None = nothing is sent."""
    response: bytes | None
    clear_counters: bool = False  # 08/01, 08/0A: applied after this request has been counted
    restart: bool = False         # 08/01
    clear_log: bool = False       # 08/01 with data FF00
    delimiter: int | None = None  # 08/03
    listen_only: bool = False     # 08/04


def _value_error(msg: str) -> ModbusError:
    return ModbusError(ExceptionCode.ILLEGAL_DATA_VALUE, msg)


def _invalid_delimiter(ch: int) -> bool:
    return ch in (ASCII_START, ASCII_CR) or chr(ch) in "0123456789ABCDEFabcdef"


def _diagnostics(pdu: bytes, unit: UnitDiagnostics, bus: BusCounters) -> DiagResult:
    if len(pdu) < 3:
        raise _value_error("missing sub-function")
    sub = struct.unpack(">H", pdu[1:3])[0]
    data = pdu[3:]
    if sub == SUB_QUERY_DATA:
        return DiagResult(bytes(pdu))
    if sub in _RESERVED_03:
        raise _value_error(f"sub-function {sub:04X} reserved")
    if sub not in _SUB_NAMES:
        raise ModbusError(ExceptionCode.ILLEGAL_FUNCTION, f"sub-function {sub:04X} not supported")
    if len(data) != 2:
        raise _value_error("data must be 2 bytes")
    value = struct.unpack(">H", data)[0]
    if sub == SUB_RESTART:
        if value not in (0x0000, RESTART_CLEAR_LOG):
            raise _value_error(f"restart data {value:04X} not 0000/FF00")
        # In Listen Only Mode the restart is executed but not answered (APP 6.8.1).
        return DiagResult(None if unit.listen_only else bytes(pdu), clear_counters=True, restart=True,
                          clear_log=value == RESTART_CLEAR_LOG)
    if sub == SUB_ASCII_DELIMITER:
        if data[1] != 0 or _invalid_delimiter(data[0]):
            raise _value_error(f"invalid ASCII delimiter {data.hex().upper()}")
        return DiagResult(bytes(pdu), delimiter=data[0])
    if value != 0:
        raise _value_error(f"data {value:04X} not 0000")
    if sub == SUB_DIAG_REGISTER:
        return DiagResult(bytes(pdu[:3]) + b"\x00\x00")
    if sub == SUB_LISTEN_ONLY:
        return DiagResult(None, listen_only=True)
    if sub == SUB_CLEAR_COUNTERS:
        return DiagResult(bytes(pdu), clear_counters=True)
    if sub == SUB_CLEAR_OVERRUN:
        bus.bus_overrun = 0
        return DiagResult(bytes(pdu))
    attr = _COUNTER_SUBS[sub]
    count = getattr(bus if attr.startswith("bus_") else unit, attr)
    return DiagResult(bytes(pdu[:3]) + struct.pack(">H", count))


def _report_server_id(identification: dict) -> bytes:
    sid = identification.get(0x01) or b""
    raw = (sid if isinstance(sid, bytes) else str(sid).encode("ascii", "replace"))[:240]
    body = raw + b"\xFF"  # run indicator ON
    return bytes([0x11, len(body)]) + body


def handle_diagnostic(pdu: bytes, unit: UnitDiagnostics, bus: BusCounters, identification: dict) -> DiagResult:
    """Execute a diagnostic request PDU (fc in ``DIAGNOSTIC_FUNCTION_CODES``)."""
    fc = pdu[0]
    try:
        if fc == 0x08:
            return _diagnostics(pdu, unit, bus)
        if len(pdu) != 1:
            raise _value_error("unexpected request data")
        if fc == 0x07:
            return DiagResult(b"\x07\x00")
        if fc == 0x0B:
            return DiagResult(struct.pack(">BHH", 0x0B, 0x0000, unit.event_count))
        if fc == 0x0C:
            events = bytes(unit.events)
            return DiagResult(struct.pack(">BBHHH", 0x0C, 6 + len(events), 0x0000, unit.event_count,
                                          bus.bus_message) + events)
        return DiagResult(_report_server_id(identification))
    except ModbusError as exc:
        return DiagResult(exception_response(fc, exc.exception_code))


def describe_diagnostic(pdu: bytes) -> str | None:
    fc = pdu[0]
    if fc == 0x08 and len(pdu) >= 3:
        sub = struct.unpack(">H", pdu[1:3])[0]
        name = _SUB_NAMES.get(sub, "reserved")
        return f"FC08 diagnostics sub={sub:02X} {name}" + (f" data={pdu[3:].hex().upper()}" if pdu[3:] else "")
    return None
