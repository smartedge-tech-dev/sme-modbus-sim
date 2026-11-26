"""ADU framing: CRC-16, MBAP (Modbus TCP) and RTU (raw RTU frames over a TCP stream).

Both framers are incremental: ``feed(data)`` buffers bytes and returns the list of
complete items, each either a ``Frame`` or a ``DroppedFrame``.

RTU over TCP has no t3.5 silence detection; frame boundaries are derived from the
function code (request side). For function codes whose length cannot be derived,
the server calls ``flush_partial()`` after a short idle period and the buffered
bytes are treated as one frame.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .constants import MBAP_HEADER_LEN, MBAP_MAX_LENGTH, MBAP_MIN_LENGTH

RTU_MAX_FRAME = 256
RTU_MIN_FRAME = 4  # unit + fc + crc(2)


def _make_crc_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC_TABLE = _make_crc_table()


def crc16(data: bytes) -> int:
    """Modbus CRC-16 (poly 0xA001 reflected, init 0xFFFF)."""
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]
    return crc


def crc_bytes(data: bytes) -> bytes:
    """CRC as transmitted on the wire: low byte first."""
    return struct.pack("<H", crc16(data))


@dataclass
class Frame:
    unit_id: int
    pdu: bytes
    raw: bytes
    transaction_id: int | None = None


@dataclass
class DroppedFrame:
    raw: bytes
    reason: str
    fatal: bool = False  # stream cannot be resynchronised; caller should close


class MbapFramer:
    def __init__(self) -> None:
        self._buf = bytearray()

    @property
    def has_partial(self) -> bool:
        return bool(self._buf)

    def feed(self, data: bytes) -> list[Frame | DroppedFrame]:
        self._buf.extend(data)
        out: list[Frame | DroppedFrame] = []
        while len(self._buf) >= 6:
            tid, pid, length = struct.unpack(">HHH", self._buf[:6])
            if not MBAP_MIN_LENGTH <= length <= MBAP_MAX_LENGTH:
                raw = bytes(self._buf)
                self._buf.clear()
                out.append(DroppedFrame(raw, f"invalid MBAP length {length}", fatal=True))
                break
            total = 6 + length
            if len(self._buf) < total:
                break
            raw = bytes(self._buf[:total])
            del self._buf[:total]
            if pid != 0:
                out.append(DroppedFrame(raw, f"protocol id {pid} != 0"))
                continue
            out.append(Frame(unit_id=raw[6], pdu=raw[MBAP_HEADER_LEN:], raw=raw, transaction_id=tid))
        return out

    def flush_partial(self) -> list[Frame | DroppedFrame]:
        # MBAP carries its own length; an incomplete ADU just waits for more data.
        return []

    @staticmethod
    def build(transaction_id: int, unit_id: int, pdu: bytes) -> bytes:
        return struct.pack(">HHHB", transaction_id & 0xFFFF, 0, len(pdu) + 1, unit_id & 0xFF) + pdu


def rtu_request_length(buf: bytes) -> int | None:
    """Expected total RTU request frame length (incl. CRC), or None if not derivable (yet).

    Returns -1 when the function code's request length is unknown.
    """
    if len(buf) < 2:
        return None
    fc = buf[1]
    if 0x01 <= fc <= 0x06:
        return 8
    if fc in (0x07, 0x0B, 0x0C, 0x11):  # unit fc crc(2)
        return 4
    if fc == 0x08:  # unit fc sub(2) data(2) crc(2); 08/00 echoes data of any length
        if len(buf) < 4:
            return None
        return -1 if buf[2:4] == b"\x00\x00" else 8
    if fc in (0x0F, 0x10):
        if len(buf) < 7:
            return None
        return 9 + buf[6]
    if fc == 0x16:  # unit fc ref(2) and(2) or(2) crc(2)
        return 10
    if fc == 0x17:  # unit fc raddr(2) rqty(2) waddr(2) wqty(2) count(1) values crc(2)
        if len(buf) < 11:
            return None
        return 13 + buf[10]
    if fc == 0x2B:
        if len(buf) < 3:
            return None
        if buf[2] == 0x0E:  # unit fc mei code object-id crc(2)
            return 7
    return -1


class RtuFramer:
    """Request-side RTU framer for a TCP byte stream.

    A frame with a bad CRC is dropped silently together with any bytes buffered after
    it (equivalent to the serial-line rule that a corrupted frame gets no response;
    over TCP we cannot detect t3.5 so we resynchronise on the next read).
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    @property
    def has_partial(self) -> bool:
        return bool(self._buf)

    def _check(self, raw: bytes) -> Frame | DroppedFrame:
        if len(raw) < RTU_MIN_FRAME:
            return DroppedFrame(raw, "frame too short")
        if crc16(raw[:-2]) != struct.unpack("<H", raw[-2:])[0]:
            return DroppedFrame(raw, "CRC mismatch")
        return Frame(unit_id=raw[0], pdu=raw[1:-2], raw=raw)

    def feed(self, data: bytes) -> list[Frame | DroppedFrame]:
        self._buf.extend(data)
        out: list[Frame | DroppedFrame] = []
        while self._buf:
            expected = rtu_request_length(self._buf)
            if expected is None or expected == -1:
                break  # wait for more bytes / idle flush
            if expected > RTU_MAX_FRAME:
                raw = bytes(self._buf)
                self._buf.clear()
                out.append(DroppedFrame(raw, f"frame length {expected} exceeds {RTU_MAX_FRAME}"))
                break
            if len(self._buf) < expected:
                break
            raw = bytes(self._buf[:expected])
            del self._buf[:expected]
            item = self._check(raw)
            if isinstance(item, DroppedFrame) and self._buf:
                rest = bytes(self._buf)
                self._buf.clear()
                item = DroppedFrame(raw + rest, item.reason)
            out.append(item)
        return out

    def flush_partial(self) -> list[Frame | DroppedFrame]:
        """Treat all buffered bytes as one frame (called by the server on idle)."""
        if not self._buf:
            return []
        raw = bytes(self._buf)
        self._buf.clear()
        return [self._check(raw)]

    @staticmethod
    def build(unit_id: int, pdu: bytes) -> bytes:
        body = bytes([unit_id & 0xFF]) + pdu
        return body + crc_bytes(body)


# ------------------------------------------------------------------- ASCII
ASCII_START = 0x3A  # ':'
ASCII_CR = 0x0D
ASCII_LF = 0x0A
ASCII_MAX_FRAME = 513  # characters, ':' to LF (SER V1.02 section 2.5.2.1)


def lrc(data: bytes) -> int:
    """Longitudinal redundancy check over the binary bytes (address .. last data byte):
    the two's complement of their 8-bit sum (SER V1.02 section 6.2.1)."""
    return (-sum(data)) & 0xFF


class AsciiFramer:
    """Request-side Modbus ASCII framer (SER V1.02 section 2.5.2).

    A frame is ``':'``, hex characters for address, PDU and LRC, then CR and the end
    delimiter (LF by default). Characters before ``':'`` are ignored; a new ``':'`` discards
    an unfinished frame. Lower-case hex digits are accepted. The inter-character timeout
    (default 1 s) is enforced by the caller through ``has_partial`` / ``flush_partial``.
    """

    def __init__(self, delimiter: int = ASCII_LF) -> None:
        self.delimiter = delimiter
        self._buf = bytearray()
        self._in_frame = False

    @property
    def has_partial(self) -> bool:
        return self._in_frame

    def _check(self, raw: bytes) -> Frame | DroppedFrame:
        body = raw[1:-2]
        if len(body) % 2:
            return DroppedFrame(raw, "odd number of hex characters")
        try:
            data = bytes.fromhex(body.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return DroppedFrame(raw, "invalid character in ASCII frame")
        if len(data) < 3:  # address + function code + LRC
            return DroppedFrame(raw, "frame too short")
        if lrc(data[:-1]) != data[-1]:
            return DroppedFrame(raw, "LRC mismatch")
        return Frame(unit_id=data[0], pdu=data[1:-1], raw=raw)

    def feed(self, data: bytes) -> list[Frame | DroppedFrame]:
        out: list[Frame | DroppedFrame] = []
        for b in data:
            if b == ASCII_START:
                if self._in_frame:
                    out.append(DroppedFrame(bytes(self._buf), "new ':' before end of frame"))
                self._buf = bytearray([b])
                self._in_frame = True
                continue
            if not self._in_frame:
                continue  # noise between frames
            self._buf.append(b)
            if b == self.delimiter and len(self._buf) >= 3 and self._buf[-2] == ASCII_CR:
                out.append(self._check(bytes(self._buf)))
                self._reset()
            elif len(self._buf) >= ASCII_MAX_FRAME:
                out.append(DroppedFrame(bytes(self._buf), f"frame longer than {ASCII_MAX_FRAME} characters"))
                self._reset()
        return out

    def _reset(self) -> None:
        self._buf = bytearray()
        self._in_frame = False

    def flush_partial(self, reason: str = "inter-character timeout") -> list[Frame | DroppedFrame]:
        """Drop the unfinished frame (called by the server when the timeout expires)."""
        if not self._in_frame:
            return []
        raw = bytes(self._buf)
        self._reset()
        return [DroppedFrame(raw, reason)]

    @staticmethod
    def build(unit_id: int, pdu: bytes, delimiter: int = ASCII_LF) -> bytes:
        data = bytes([unit_id & 0xFF]) + pdu
        return b":" + (data + bytes([lrc(data)])).hex().upper().encode("ascii") + bytes([ASCII_CR, delimiter])
