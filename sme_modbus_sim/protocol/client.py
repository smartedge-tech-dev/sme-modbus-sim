"""Minimal asynchronous READ-ONLY Modbus client (scan / record, design 20261125-0003).

Safety rule: this client only ever sends FC 01 / 02 / 03 / 04 and FC 2B / MEI 0E (Read Device
Identification). There is deliberately no write method, and ``_request`` refuses any other function code
before anything reaches the wire.

Transports (field names follow ``ListenerConfig``):
  * ``tcp``           Modbus TCP (MBAP), transaction ids checked
  * ``rtu_over_tcp``  raw RTU frames (CRC) over a TCP stream
  * ``udp``           one MBAP ADU per UDP datagram, transaction ids checked
  * ``rtu``           serial line via pyserial; blocking I/O runs in a worker thread (like ``serial_server``)

Every request has a timeout and goes through a rate limiter (requests per second). Exception responses
raise ``ModbusError(code)``; a missing answer raises ``ResponseTimeout``; malformed answers (CRC mismatch,
wrong unit / function code, bad length) raise ``BadResponse``.
"""
from __future__ import annotations

import asyncio
import socket
import ssl
import struct
import time
from typing import Any, Callable, Dict, List, Optional

from .constants import MAX_READ_BITS, MAX_READ_REGISTERS, MEI_READ_DEVICE_ID
from .errors import ModbusError
from .framer import MbapFramer, RtuFramer, crc16
from .tls import client_context

READ_ONLY_FUNCTION_CODES = frozenset({0x01, 0x02, 0x03, 0x04, 0x2B})
TRANSPORTS = ("tcp", "rtu_over_tcp", "udp", "tls", "rtu")
TABLE_FC = {"coil": 0x01, "discrete": 0x02, "holding": 0x03, "input": 0x04}

# Read Device Identification object ids (Application Protocol V1.1b3 table 6.21)
DEVICE_ID_OBJECTS = {0x00: "vendor_name", 0x01: "product_code", 0x02: "revision", 0x03: "vendor_url",
                     0x04: "product_name", 0x05: "model_name", 0x06: "user_application_name"}


class ClientError(Exception):
    """Transport-level failure (no Modbus exception code)."""


class ResponseTimeout(ClientError):
    pass


class BadResponse(ClientError):
    pass


def rtu_response_length(buf: bytes) -> Optional[int]:
    """Total length (incl. CRC) of the RTU *response* starting at ``buf``; None = need more bytes.

    Raises ``BadResponse`` for a function code whose response length cannot be derived.
    """
    if len(buf) < 2:
        return None
    fc = buf[1]
    if fc & 0x80:
        return 5  # unit fc exc crc(2)
    if fc in (0x01, 0x02, 0x03, 0x04):
        return None if len(buf) < 3 else 5 + buf[2]
    if fc == 0x2B:
        if len(buf) < 8:  # unit fc mei code conformity more next count
            return None
        pos = 8
        for _ in range(buf[7]):
            if len(buf) < pos + 2:
                return None
            pos += 2 + buf[pos + 1]
        return pos + 2
    raise BadResponse(f"unexpected function code 0x{fc:02X} in response")


def _check_response(fc: int, pdu: bytes) -> bytes:
    if not pdu:
        raise BadResponse("empty response")
    if pdu[0] == (fc | 0x80):
        if len(pdu) < 2:
            raise BadResponse("truncated exception response")
        raise ModbusError(pdu[1])
    if pdu[0] != fc:
        raise BadResponse(f"function code 0x{pdu[0]:02X} in response to 0x{fc:02X}")
    return pdu


class ModbusClient:
    """One connection to one target; use ``async with`` or ``connect()`` / ``close()``."""

    def __init__(self, transport: str = "tcp", host: str = "127.0.0.1", port: Any = 502, *,
                 baudrate: int = 19200, bytesize: int = 8, parity: str = "E", stopbits: Optional[int] = None,
                 timeout: float = 1.0, rate: float = 10.0,
                 serial_factory: Optional[Callable[..., Any]] = None,
                 ca: Optional[str] = None, certificate: Optional[str] = None,
                 private_key: Optional[str] = None) -> None:
        if transport not in TRANSPORTS:
            raise ValueError(f"transport must be one of {', '.join(TRANSPORTS)}")
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        if rate <= 0:
            raise ValueError("rate must be > 0 requests/s")
        self.transport = transport
        self.host = host
        self.port = port
        self.baudrate = int(baudrate)
        self.bytesize = int(bytesize)
        self.parity = parity
        self.stopbits = int(stopbits if stopbits is not None else (2 if parity == "N" else 1))
        self.timeout = float(timeout)
        self.rate = float(rate)
        self.serial_factory = serial_factory
        self._ssl = None
        if transport == "tls":  # Modbus/TCP Security: server verified against ca, client cert optional
            try:
                self._ssl = client_context(ca, certificate, private_key)
            except (OSError, ssl.SSLError) as e:
                raise ValueError(f"cannot load TLS ca / certificate / key: {getattr(e, 'strerror', None) or e}") from None
        elif ca is not None or certificate is not None or private_key is not None:
            raise ValueError("ca / certificate / private_key only apply to transport tls")
        self.requests = 0
        self._tid = 0
        self._last_send = 0.0
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._serial: Any = None
        self._udp: Any = None  # connected non-blocking UDP socket
        self._dirty = False
        self._lock = asyncio.Lock()

    @property
    def endpoint(self) -> str:
        if self.transport == "rtu":
            return f"rtu {self.port} {self.baudrate} {self.bytesize}{self.parity}{self.stopbits}"
        return f"{self.transport} {self.host}:{self.port}"

    # ------------------------------------------------------------ connection
    async def connect(self) -> None:
        if self.transport == "rtu":
            factory = self.serial_factory
            if factory is None:
                import serial  # pyserial

                factory = serial.serial_for_url
            self._serial = await asyncio.to_thread(
                factory, self.port, baudrate=self.baudrate, bytesize=self.bytesize, parity=self.parity,
                stopbits=self.stopbits, timeout=0.02)
            return
        if self.transport == "udp":
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(self.host, int(self.port),
                                                                     type=socket.SOCK_DGRAM)
                fam, _, proto, _, addr = infos[0]
                sock = socket.socket(fam, socket.SOCK_DGRAM, proto)
                sock.setblocking(False)
                try:
                    sock.connect(addr)
                except OSError:
                    sock.close()
                    raise
                self._udp = sock
            except OSError as e:
                raise ClientError(f"cannot reach {self.host}:{self.port}: {e.strerror or e}") from None
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, int(self.port), ssl=self._ssl), self.timeout)
        except asyncio.TimeoutError:
            raise ResponseTimeout(f"connect to {self.host}:{self.port} timed out") from None
        except ssl.SSLError as e:
            raise ClientError(f"TLS handshake with {self.host}:{self.port} failed: {e.reason or e}") from None
        except OSError as e:
            raise ClientError(f"cannot connect to {self.host}:{self.port}: {e.strerror or e}") from None

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (OSError, ConnectionError):
                pass
            self._writer = self._reader = None
        if self._udp is not None:
            self._udp.close()
            self._udp = None
        if self._serial is not None:
            s, self._serial = self._serial, None
            await asyncio.to_thread(s.close)

    async def __aenter__(self) -> "ModbusClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------ public read API
    async def read_coils(self, unit: int, address: int, count: int) -> List[int]:
        return await self._read_bits(0x01, unit, address, count)

    async def read_discrete_inputs(self, unit: int, address: int, count: int) -> List[int]:
        return await self._read_bits(0x02, unit, address, count)

    async def read_holding_registers(self, unit: int, address: int, count: int) -> List[int]:
        return await self._read_registers(0x03, unit, address, count)

    async def read_input_registers(self, unit: int, address: int, count: int) -> List[int]:
        return await self._read_registers(0x04, unit, address, count)

    async def read(self, table: str, unit: int, address: int, count: int) -> List[int]:
        fc = TABLE_FC[table]
        if fc in (0x01, 0x02):
            return await self._read_bits(fc, unit, address, count)
        return await self._read_registers(fc, unit, address, count)

    async def read_device_identification(self, unit: int, code: int = 0x01) -> Dict[int, str]:
        """Stream access (code 01 basic / 02 regular / 03 extended); follows More Follows (max 16 pages)."""
        objects: Dict[int, str] = {}
        object_id = 0
        for _ in range(16):
            pdu = await self._request(unit, bytes([0x2B, MEI_READ_DEVICE_ID, code, object_id]))
            if len(pdu) < 7 or pdu[1] != MEI_READ_DEVICE_ID:
                raise BadResponse("malformed Read Device Identification response")
            more, next_id, count = pdu[4], pdu[5], pdu[6]
            pos = 7
            for _ in range(count):
                if pos + 2 > len(pdu) or pos + 2 + pdu[pos + 1] > len(pdu):
                    raise BadResponse("truncated device identification object")
                oid, ln = pdu[pos], pdu[pos + 1]
                objects[oid] = pdu[pos + 2:pos + 2 + ln].decode("ascii", errors="replace")
                pos += 2 + ln
            if more != 0xFF or next_id == object_id:
                break
            object_id = next_id
        return objects

    # ------------------------------------------------------------ internals
    async def _read_bits(self, fc: int, unit: int, address: int, count: int) -> List[int]:
        if not 1 <= count <= MAX_READ_BITS or not 0 <= address <= 0xFFFF or address + count > 0x10000:
            raise ValueError(f"bad bit read {address}+{count}")
        pdu = await self._request(unit, struct.pack(">BHH", fc, address, count))
        nbytes = (count + 7) // 8
        if len(pdu) < 2 or pdu[1] != nbytes or len(pdu) != 2 + nbytes:
            raise BadResponse(f"byte count mismatch for {count} bits")
        return [(pdu[2 + i // 8] >> (i % 8)) & 1 for i in range(count)]

    async def _read_registers(self, fc: int, unit: int, address: int, count: int) -> List[int]:
        if not 1 <= count <= MAX_READ_REGISTERS or not 0 <= address <= 0xFFFF or address + count > 0x10000:
            raise ValueError(f"bad register read {address}+{count}")
        pdu = await self._request(unit, struct.pack(">BHH", fc, address, count))
        if len(pdu) < 2 or pdu[1] != 2 * count or len(pdu) != 2 + 2 * count:
            raise BadResponse(f"byte count mismatch for {count} registers")
        return list(struct.unpack(f">{count}H", pdu[2:]))

    async def _throttle(self) -> None:
        wait = self._last_send + 1.0 / self.rate - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_send = time.monotonic()

    async def _request(self, unit: int, pdu: bytes) -> bytes:
        fc = pdu[0]
        if fc not in READ_ONLY_FUNCTION_CODES or (fc == 0x2B and pdu[1:2] != bytes([MEI_READ_DEVICE_ID])):
            raise PermissionError(f"read-only client refuses function code 0x{fc:02X}")  # hard safety rule
        async with self._lock:
            await self._throttle()
            self.requests += 1
            if self.transport in ("tcp", "tls"):
                resp = await self._tcp_exchange(unit, pdu)
            elif self.transport == "rtu_over_tcp":
                resp = await self._rtu_tcp_exchange(unit, pdu)
            elif self.transport == "udp":
                resp = await self._udp_exchange(unit, pdu)
            else:
                resp = await asyncio.to_thread(self._serial_exchange, unit, pdu)
        return _check_response(fc, resp)

    def _stream(self) -> tuple:
        if self._reader is None or self._writer is None:
            raise ClientError("not connected")
        return self._reader, self._writer

    async def _tcp_exchange(self, unit: int, pdu: bytes) -> bytes:
        reader, writer = self._stream()
        self._tid = (self._tid + 1) & 0xFFFF
        tid = self._tid
        writer.write(MbapFramer.build(tid, unit, pdu))
        await writer.drain()
        deadline = time.monotonic() + self.timeout
        while True:  # skip stale answers to earlier (timed out) requests
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResponseTimeout(f"no response within {self.timeout:g} s")
            try:
                head = await asyncio.wait_for(reader.readexactly(7), remaining)
                rtid, pid, length, runit = struct.unpack(">HHHB", head)
                if not 2 <= length <= 254 or pid != 0:
                    raise BadResponse(f"invalid MBAP header (protocol {pid}, length {length})")
                body = await asyncio.wait_for(reader.readexactly(length - 1), max(remaining, 0.01))
            except asyncio.TimeoutError:
                raise ResponseTimeout(f"no response within {self.timeout:g} s") from None
            except asyncio.IncompleteReadError:
                raise ClientError("connection closed by the target") from None
            if rtid == tid:
                if runit != unit:
                    raise BadResponse(f"unit id {runit} in response to unit {unit}")
                return body

    async def _udp_exchange(self, unit: int, pdu: bytes) -> bytes:
        if self._udp is None:
            raise ClientError("not connected")
        sock, loop = self._udp, asyncio.get_running_loop()
        while True:  # late answers to earlier (timed out) requests
            try:
                sock.recv(65535)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:  # e.g. Windows ConnectionResetError from an earlier ICMP port unreachable
                continue
        self._tid = (self._tid + 1) & 0xFFFF
        tid = self._tid
        try:
            sock.send(MbapFramer.build(tid, unit, pdu))
        except OSError as e:
            raise ClientError(f"cannot send to {self.host}:{self.port}: {e.strerror or e}") from None
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResponseTimeout(f"no response within {self.timeout:g} s")
            try:
                data = await asyncio.wait_for(loop.sock_recv(sock, 65535), remaining)
            except asyncio.TimeoutError:
                raise ResponseTimeout(f"no response within {self.timeout:g} s") from None
            except (ConnectionResetError, ConnectionRefusedError):
                # ICMP port unreachable: nothing listens there (yet); wait out the timeout like a master
                await asyncio.sleep(min(0.05, max(remaining, 0)))
                continue
            if len(data) < 8:
                raise BadResponse(f"datagram too short for MBAP ({len(data)} bytes)")
            rtid, pid, length, runit = struct.unpack(">HHHB", data[:7])
            if pid != 0 or 6 + length != len(data):
                raise BadResponse(f"invalid MBAP header (protocol {pid}, length {length}, datagram {len(data)} bytes)")
            if rtid == tid:
                if runit != unit:
                    raise BadResponse(f"unit id {runit} in response to unit {unit}")
                return data[7:]

    async def _drain(self, reader: asyncio.StreamReader) -> None:
        while True:
            try:
                if not await asyncio.wait_for(reader.read(256), 0.05):
                    return
            except asyncio.TimeoutError:
                return

    async def _rtu_tcp_exchange(self, unit: int, pdu: bytes) -> bytes:
        reader, writer = self._stream()
        if self._dirty:  # late bytes from a timed out request would desynchronise the stream
            await self._drain(reader)
            self._dirty = False
        writer.write(RtuFramer.build(unit, pdu))
        await writer.drain()
        buf = bytearray()
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                need = rtu_response_length(bytes(buf))
                if need is not None and len(buf) >= need:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                chunk = await asyncio.wait_for(reader.read(256), remaining)
                if not chunk:
                    raise ClientError("connection closed by the target")
                buf.extend(chunk)
        except asyncio.TimeoutError:
            self._dirty = True
            raise ResponseTimeout(f"no response within {self.timeout:g} s") from None
        except BadResponse:
            self._dirty = True
            raise
        if len(buf) > need:
            self._dirty = True
        return self._check_rtu(unit, bytes(buf[:need]))

    def _serial_exchange(self, unit: int, pdu: bytes) -> bytes:
        s = self._serial
        if s is None:
            raise ClientError("not connected")
        reset = getattr(s, "reset_input_buffer", None)
        if reset:
            reset()
        s.write(RtuFramer.build(unit, pdu))
        buf = bytearray()
        deadline = time.monotonic() + self.timeout
        while True:
            need = rtu_response_length(bytes(buf))
            if need is not None and len(buf) >= need:
                break
            if time.monotonic() >= deadline:
                raise ResponseTimeout(f"no response within {self.timeout:g} s")
            buf.extend(s.read((need - len(buf)) if need else 1) or b"")
        return self._check_rtu(unit, bytes(buf[:need]))

    @staticmethod
    def _check_rtu(unit: int, frame: bytes) -> bytes:
        if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
            raise BadResponse("CRC mismatch")
        if frame[0] != unit:
            raise BadResponse(f"unit id {frame[0]} in response to unit {unit}")
        return frame[1:-2]
