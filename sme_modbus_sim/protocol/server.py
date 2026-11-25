"""asyncio Modbus TCP / RTU-over-TCP server.

Unit id routing:
  * ``unit_id_mode="any"``    -- every request goes to ``default_unit`` (RTU unit 0 is still broadcast).
  * ``unit_id_mode="strict"`` -- only units known to ``resolver`` answer.
      - TCP: unit 0 and 255 are aliases for ``default_unit`` when it is set
        (Messaging Implementation Guide recommends 0xFF for direct connection).
      - Unknown unit: ``unknown_unit="ignore"`` (default) sends nothing;
        ``unknown_unit="exception"`` answers exception 0B on TCP. This is a
        SIMULATOR-SPECIFIC choice: the spec reserves 0B for gateways, but some field
        devices / gateways behave this way. RTU over TCP never answers unknown units.
  * RTU over TCP unit 0 = broadcast (Serial Line V1.02): write FCs are executed on all
    units returned by ``all_units``; no response is sent. Non-write broadcasts are ignored.

Each connection handles its requests sequentially; many connections run concurrently.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from .constants import ExceptionCode
from .framer import DroppedFrame, Frame, MbapFramer, RtuFramer
from .pdu import describe_request, describe_response, exception_response, handle_pdu, is_write
from .store import UnitStore

FRAMERS = ("tcp", "rtu_over_tcp")
UNIT_ID_MODES = ("strict", "any")
UNKNOWN_UNIT_POLICIES = ("ignore", "exception")
RTU_IDLE_FLUSH_S = 0.05


@dataclass
class TraceEvent:
    ts: float
    listener_id: str
    peer: str
    direction: str  # 'rx' | 'tx'
    raw: str  # hex
    unit_id: int | None
    function_code: int | None
    summary: str
    exception_code: int | None = None
    error: str | None = None


Resolver = Callable[[int], "UnitStore | None"]
PduHandler = Callable[[UnitStore, bytes], bytes]


class ModbusTcpServer:
    def __init__(
        self,
        host: str,
        port: int,
        framer: str = "tcp",
        resolver: Resolver | None = None,
        unit_id_mode: str = "strict",
        default_unit: int | None = None,
        trace: Callable[[TraceEvent], None] | None = None,
        *,
        listener_id: str = "",
        unknown_unit: str = "ignore",
        all_units: Callable[[], Iterable[UnitStore]] | None = None,
        pdu_handler: PduHandler = handle_pdu,
    ) -> None:
        if framer not in FRAMERS:
            raise ValueError(f"invalid framer: {framer!r}")
        if unit_id_mode not in UNIT_ID_MODES:
            raise ValueError(f"invalid unit_id_mode: {unit_id_mode!r}")
        if unknown_unit not in UNKNOWN_UNIT_POLICIES:
            raise ValueError(f"invalid unknown_unit: {unknown_unit!r}")
        if unit_id_mode == "any" and default_unit is None:
            raise ValueError("unit_id_mode='any' requires default_unit")
        self.host = host
        self.port = port
        self.framer = framer
        self.resolver: Resolver = resolver or (lambda _uid: None)
        self.unit_id_mode = unit_id_mode
        self.default_unit = default_unit
        self.trace = trace
        self.listener_id = listener_id
        self.unknown_unit = unknown_unit
        self.all_units = all_units
        self.pdu_handler = pdu_handler
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ lifecycle
    @property
    def bound_port(self) -> int:
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self.port

    @property
    def client_count(self) -> int:
        return len(self._writers)

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.bound_port}"

    @property
    def status(self) -> str:
        return "running" if self._server is not None else "stopped"

    error: str | None = None  # bind errors are raised by start(); kept for parity with the serial server

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_client, self.host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for w in list(self._writers):
            with contextlib.suppress(Exception):
                w.close()
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server is not None:
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    # ---------------------------------------------------------------- trace
    def _emit(self, peer: str, direction: str, raw: bytes, unit_id, fc, summary: str,
              exception_code: int | None = None, error: str | None = None) -> None:
        if self.trace is None:
            return
        ev = TraceEvent(time.time(), self.listener_id, peer, direction, raw.hex(" "),
                        unit_id, fc, summary, exception_code, error)
        with contextlib.suppress(Exception):
            self.trace(ev)

    # -------------------------------------------------------------- routing
    def _resolve(self, unit_id: int) -> UnitStore | None:
        if self.unit_id_mode == "any":
            return self.resolver(self.default_unit)
        if self.framer == "tcp" and unit_id in (0, 255) and self.default_unit is not None:
            return self.resolver(self.default_unit)
        return self.resolver(unit_id)

    def process_frame(self, frame: Frame) -> bytes | None:
        """Return the response PDU for ``frame`` or None when no response is sent."""
        pdu = frame.pdu
        if not pdu:
            return None
        fc = pdu[0]
        if self.framer == "rtu_over_tcp" and frame.unit_id == 0:
            if is_write(fc) and self.all_units is not None:
                for store in self.all_units():
                    self.pdu_handler(store, pdu)
            return None
        store = self._resolve(frame.unit_id)
        if store is None:
            if self.framer == "tcp" and self.unknown_unit == "exception":
                return exception_response(fc, ExceptionCode.GATEWAY_TARGET_NO_RESPONSE)
            return None
        return self.pdu_handler(store, pdu)

    # ----------------------------------------------------------- connection
    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self._writers.add(writer)
        peername = writer.get_extra_info("peername")
        peer = f"{peername[0]}:{peername[1]}" if peername else "?"
        framer = MbapFramer() if self.framer == "tcp" else RtuFramer()
        try:
            while True:
                if framer.has_partial and self.framer == "rtu_over_tcp":
                    try:
                        data = await asyncio.wait_for(reader.read(4096), RTU_IDLE_FLUSH_S)
                    except asyncio.TimeoutError:
                        items = framer.flush_partial()
                        if not await self._handle_items(items, framer, writer, peer):
                            break
                        continue
                else:
                    data = await reader.read(4096)
                if not data:
                    break
                if not await self._handle_items(framer.feed(data), framer, writer, peer):
                    break
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except asyncio.CancelledError:
            pass
        finally:
            self._writers.discard(writer)
            with contextlib.suppress(Exception):
                writer.close()
            if task is not None:
                self._tasks.discard(task)

    async def _handle_items(self, items, framer, writer: asyncio.StreamWriter, peer: str) -> bool:
        """Process framer output; return False when the connection must be closed."""
        for item in items:
            if isinstance(item, DroppedFrame):
                self._emit(peer, "rx", item.raw, None, None, "dropped", error=item.reason)
                if item.fatal:
                    return False
                continue
            fc = item.pdu[0] if item.pdu else None
            self._emit(peer, "rx", item.raw, item.unit_id, fc, describe_request(item.pdu))
            resp = self.process_frame(item)
            if resp is None:
                continue
            if isinstance(framer, MbapFramer):
                raw = MbapFramer.build(item.transaction_id or 0, item.unit_id, resp)
            else:
                raw = RtuFramer.build(item.unit_id, resp)
            summary, exc = describe_response(resp)
            self._emit(peer, "tx", raw, item.unit_id, resp[0] & 0x7F, summary, exc)
            writer.write(raw)
            await writer.drain()
        return True
