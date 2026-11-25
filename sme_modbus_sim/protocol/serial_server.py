"""Modbus RTU / ASCII server on a serial line (pyserial), Modbus over Serial Line V1.02.

Threading model: one reader thread owns the serial port (reads *and* writes, so a slow
``write`` at low baud rates never blocks the asyncio loop). Complete frames are handed to
the asyncio loop with ``call_soon_threadsafe``; the loop runs the PDU against the unit
stores (which the engine also mutates on that loop) and queues the response back to the
reader thread.

Frame delimitation (SER section 2.5.1.1):
  * a frame ends after a silence of t3.5. Character time = 11 bits / baudrate; above
    19200 baud the fixed values t1.5 = 750 us and t3.5 = 1.75 ms are used.
  * the RtuFramer length logic completes frames of known function codes immediately,
    without waiting for the silence.
  * ``strict_timing=True``: a gap > t1.5 inside a frame drops the frame, and t3.5 is used
    as-is for the end-of-frame silence. Default False, because USB-serial adapters and OS
    scheduling deliver bytes in bursts (latency timers of 1-16 ms); in lenient mode the
    end-of-frame silence is max(t3.5, ``LENIENT_SILENCE_S``) and t1.5 is not checked.

ASCII (SER section 2.5.2, ``framer="ascii"``): frames are delimited by ':' and CR LF, so
no silence detection; an unfinished frame is dropped when no character arrives for
``char_timeout`` (default 1 s).

Addressing (SER section 2.2): 1..247 individual, 0 broadcast (write FCs executed on every
unit of the listener, never answered), 248..255 reserved and ignored.

Fault injection: as in ``server.py``; ``disconnect`` is treated as no response (a serial line has
no connection), ``delay`` queues the response with ``call_later`` so the reader keeps running.
"""
from __future__ import annotations

import asyncio
import contextlib
import queue
import threading
import time
from typing import Callable, Iterable

from .framer import AsciiFramer, DroppedFrame, Frame, RtuFramer
from .pty_port import PTY_PORT, PtyPort
from .pdu import describe_request, describe_response, exception_response, handle_pdu, is_write
from .server import UNIT_ID_MODES, PduHandler, Resolver, TraceEvent, encode_response
from .store import UnitStore

PARITIES = ("E", "N", "O")
LENIENT_SILENCE_S = 0.02
MAX_POLL_S = 0.01
SERIAL_MAX_UNIT = 247


def char_time(baudrate: int) -> float:
    return 11.0 / baudrate  # start + 8 data + parity/stop + stop = 11 bits


def t15(baudrate: int) -> float:
    return 0.00075 if baudrate > 19200 else 1.5 * char_time(baudrate)


def t35(baudrate: int) -> float:
    return 0.00175 if baudrate > 19200 else 3.5 * char_time(baudrate)


SERIAL_FRAMERS = ("rtu", "ascii")
ASCII_CHAR_TIMEOUT_S = 1.0


class ModbusSerialServer:
    host = None

    def __init__(
        self,
        port: str,
        baudrate: int = 19200,
        bytesize: int = 8,
        parity: str = "E",
        stopbits: int = 1,
        resolver: Resolver | None = None,
        unit_id_mode: str = "strict",
        default_unit: int | None = None,
        trace: Callable[[TraceEvent], None] | None = None,
        *,
        listener_id: str = "",
        all_units: Callable[[], Iterable[UnitStore]] | None = None,
        pdu_handler: PduHandler = handle_pdu,
        strict_timing: bool = False,
        serial_factory: Callable[..., object] | None = None,
        faults=None,
        link: str | None = None,
        framer: str = "rtu",
        char_timeout: float = ASCII_CHAR_TIMEOUT_S,
    ) -> None:
        if framer not in SERIAL_FRAMERS:
            raise ValueError(f"invalid serial framer: {framer!r}")
        if parity not in PARITIES:
            raise ValueError(f"invalid parity: {parity!r}")
        if stopbits not in (1, 2):
            raise ValueError(f"invalid stopbits: {stopbits!r}")
        if unit_id_mode not in UNIT_ID_MODES:
            raise ValueError(f"invalid unit_id_mode: {unit_id_mode!r}")
        if unit_id_mode == "any" and default_unit is None:
            raise ValueError("unit_id_mode='any' requires default_unit")
        self.port = port
        self.baudrate = int(baudrate)
        self.bytesize = int(bytesize)
        self.parity = parity
        self.stopbits = int(stopbits)
        self.resolver: Resolver = resolver or (lambda _uid: None)
        self.unit_id_mode = unit_id_mode
        self.default_unit = default_unit
        self.trace = trace
        self.listener_id = listener_id
        self.all_units = all_units
        self.pdu_handler = pdu_handler
        self.faults = faults
        self.strict_timing = strict_timing and framer == "rtu"
        self.serial_factory = serial_factory
        self.link = link
        self.framer = framer
        self.char_timeout = float(char_timeout)
        self.device: str | None = None  # pty: the slave end the device under test opens
        self.t15 = t15(self.baudrate)
        self.t35 = t35(self.baudrate)
        if framer == "ascii":
            self.silence = self.char_timeout  # only used to drop an unfinished frame
        else:
            self.silence = self.t35 if strict_timing else max(self.t35, LENIENT_SILENCE_S)
        self.error: str | None = None
        self._serial = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._tx: queue.SimpleQueue[bytes] = queue.SimpleQueue()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ properties
    @property
    def endpoint(self) -> str:
        if self.is_pty:
            return f"pty {self.display_port}" + (f" -> {self.device}" if self.link and self.device else "")
        return f"{self.port} {self.baudrate} {self.bytesize}{self.parity}{self.stopbits}" +             (" ascii" if self.framer == "ascii" else "")

    @property
    def is_pty(self) -> bool:
        return self.port == PTY_PORT

    @property
    def display_port(self) -> str:
        """Port name to show: for a pty, the path the device under test opens."""
        if self.is_pty:
            return self.link or self.device or PTY_PORT
        return self.port

    @property
    def bound_port(self) -> None:
        return None

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        return "running" if self._serial is not None else "stopped"

    @property
    def client_count(self) -> int:
        return 1 if self._serial is not None and not self.error else 0

    # ------------------------------------------------------------ lifecycle
    def _open(self):
        if self.is_pty and self.serial_factory is None:
            ser = PtyPort(self.link, timeout=self._poll_s())
            self.device = ser.device
            return ser
        if self.serial_factory is not None:
            factory = self.serial_factory
        else:
            import serial  # pyserial; imported lazily so TCP-only setups never need a port

            factory = serial.serial_for_url
        return factory(self.port, baudrate=self.baudrate, bytesize=self.bytesize, parity=self.parity,
                       stopbits=self.stopbits, timeout=self._poll_s())

    def _poll_s(self) -> float:
        return MAX_POLL_S if self.framer == "ascii" else min(max(self.silence / 2, 0.001), MAX_POLL_S)

    async def start(self) -> None:
        """Open the port and start the reader thread. Never raises: failures go to ``error``."""
        self._loop = asyncio.get_running_loop()
        self.error = None
        self._stop.clear()
        try:
            self._serial = await self._loop.run_in_executor(None, self._open)
        except Exception as e:  # noqa: BLE001 - SerialException, OSError, ValueError ...
            self._serial = None
            self.error = (f"cannot create pty: {e}" if self.is_pty
                          else f"cannot open serial port {self.port!r}: {e}")
            return
        self._thread = threading.Thread(target=self._reader, name=f"modbus-rtu-{self.listener_id}", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        ser, self._serial = self._serial, None
        if ser is not None:
            with contextlib.suppress(Exception):
                ser.cancel_read()
        if self._thread is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._thread.join, 2.0)
            self._thread = None
        if ser is not None:
            with contextlib.suppress(Exception):
                ser.close()

    # --------------------------------------------------------- reader thread
    def _reader(self) -> None:
        ser = self._serial
        framer = AsciiFramer() if self.framer == "ascii" else RtuFramer()
        last = 0.0
        while not self._stop.is_set() and ser is not None:
            items: list[Frame | DroppedFrame] = []
            try:
                while True:
                    try:
                        raw = self._tx.get_nowait()
                    except queue.Empty:
                        break
                    ser.write(raw)
                data = ser.read(ser.in_waiting or 1)
            except Exception as e:  # noqa: BLE001 - port unplugged / closed
                if not self._stop.is_set() and self._loop is not None:
                    self._loop.call_soon_threadsafe(self._fail, f"serial port {self.port!r}: {e}")
                return
            now = time.perf_counter()
            if data:
                if framer.has_partial:
                    gap = now - last
                    if gap >= self.silence:
                        items += framer.flush_partial()
                    elif self.strict_timing and gap > self.t15:
                        partial = b"".join(i.raw for i in framer.flush_partial())
                        items.append(DroppedFrame(partial, f"inter-character gap {gap * 1000:.2f} ms > t1.5"))
                items += framer.feed(data)
                last = now
            elif framer.has_partial and now - last >= self.silence:
                items += framer.flush_partial()
            if items and self._loop is not None:
                with contextlib.suppress(RuntimeError):  # loop closed during shutdown
                    self._loop.call_soon_threadsafe(self._deliver, items)

    def _fail(self, message: str) -> None:
        self.error = message

    # ---------------------------------------------------------- loop thread
    def _emit(self, direction: str, raw: bytes, unit_id, fc, summary: str,
              exception_code: int | None = None, error: str | None = None, fault: dict | None = None) -> None:
        if self.trace is None:
            return
        ev = TraceEvent(time.time(), self.listener_id, self.port, direction, raw.hex(" "),
                        unit_id, fc, summary, exception_code, error, fault)
        with contextlib.suppress(Exception):
            self.trace(ev)

    def process_frame(self, frame: Frame) -> bytes | None:
        """Return the response PDU for ``frame`` or None when no response is sent."""
        pdu = frame.pdu
        if not pdu:
            return None
        uid = frame.unit_id
        if uid == 0:
            if is_write(pdu[0]) and self.all_units is not None:
                for store in self.all_units():
                    self.pdu_handler(store, pdu)
            return None
        if uid > SERIAL_MAX_UNIT:
            return None
        store = self.resolver(self.default_unit if self.unit_id_mode == "any" else uid)
        return None if store is None else self.pdu_handler(store, pdu)

    def _deliver(self, items: list[Frame | DroppedFrame]) -> None:
        for item in items:
            if isinstance(item, DroppedFrame):
                self._emit("rx", item.raw, None, None, "dropped", error=item.reason)
                continue
            fc = item.pdu[0] if item.pdu else None
            hit = None
            if self.faults is not None and item.pdu and 0 < item.unit_id <= SERIAL_MAX_UNIT:
                hit = self.faults.evaluate(self.listener_id, self.framer, item.unit_id, item.pdu)
            self._emit("rx", item.raw, item.unit_id, fc, describe_request(item.pdu),
                       fault=hit.info if hit is not None else None)
            if hit is not None and hit.action in ("no_response", "disconnect"):
                continue
            if hit is not None and hit.code is not None:
                resp = exception_response(fc, hit.code)
            else:
                resp = self.process_frame(item)
            if resp is None or self._serial is None:
                continue
            raw = encode_response(self.framer, 0, item.unit_id, resp, hit)
            if hit is not None and hit.delay and self._loop is not None:
                self._loop.call_later(hit.delay, self._send, raw, item.unit_id, resp, hit)
            else:
                self._send(raw, item.unit_id, resp, hit)

    def _send(self, raw: bytes, unit_id: int, resp: bytes, hit=None) -> None:
        if self._serial is None:
            return
        summary, exc = describe_response(resp)
        self._emit("tx", raw, unit_id, resp[0] & 0x7F, summary, exc, fault=hit.info if hit is not None else None)
        self._tx.put(raw)
