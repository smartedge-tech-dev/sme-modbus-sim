"""Simulation engine: register images, point <-> register mapping, tick loop, listeners, trace, push.

The generic part (subscriber queues, log events, validate -> build -> swap -> roll back reload, run / pause /
time scale, tick + stats push) comes from ``sme_sim_common.engine.BaseEngine``; this module adds the Modbus
specific parts (design §3, §5.1-5.5): register images, listeners, protocol write validation, trace, faults.

Value model:
  * Each point keeps an engineering ``value`` (float precision for numeric types, so slow counters
    accumulate correctly) and mirrors it into the unit's register image on every change.
    Integer types are rounded into the register; the value reported to the UI/API is what a master
    reads (decoded from the registers).
  * Behaviors see same-device points by ``ident(name)`` plus ``t``, ``dt``, ``hour`` and ``pv(dev, point)``.
    Results are clamped to ``min``/``max``; integer types wrap at the datatype range for ``counter`` /
    ``pulse`` behaviors and saturate for every other behavior. A failing behavior records ``error`` on
    the point and the simulation continues.
  * Protocol writes (FC 05/06/0F/10) are checked before they apply: a write covering only part of a
    multi-register point, or touching an ``access: r`` point -> exception 02; a value outside ``min``/``max``
    -> exception 03 (simulator-specific). After the write the registers are decoded into the point value.
    A point that also has a behavior takes the written value immediately; the behavior keeps running
    from that value on the next tick (so a written ``expr`` point is overwritten again on the next tick).
  * Modbus is served while the simulation is paused (only behaviors stop).
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from sme_sim_common.engine import BaseEngine

from . import __version__
from .behaviors import Behavior, ident, make_behavior
from .config import BIT_TABLES, DeviceConfig, IdentificationConfig, PointConfig, SERIAL_TYPES, SimConfig, dump_yaml, register_count
from .faults import FaultManager
from .protocol import datatypes as dtypes
from .protocol.constants import ExceptionCode
from .protocol.errors import ModbusError
from .protocol.serial_server import ModbusSerialServer
from .protocol.server import ModbusTcpServer, TraceEvent
from .protocol.store import UnitStore
from . import templates as tpl
from .templates import expand_device


TRACE_SIZE = 2000
EVENT_SIZE = 200
QUEUE_SIZE = 1000
STATS_INTERVAL = 2.0  # seconds
WRAPPING_BEHAVIORS = ("counter", "pulse")
INT_TYPES = ("uint16", "int16", "uint32", "int32", "uint64", "int64")
FLOAT_TYPES = ("float32", "float64")
# SunSpec "not implemented" raw values (as decoded with scale 1); float types use NaN
NA_RAW = {"int16": -0x8000, "uint16": 0xFFFF, "int32": -0x80000000, "uint32": 0xFFFFFFFF,
          "int64": -0x8000000000000000, "uint64": 0xFFFFFFFFFFFFFFFF}


class PointRuntime:
    """One configured point bound to its addresses in a ``UnitStore``."""

    def __init__(self, dev: "DeviceRuntime", cfg: PointConfig, base_dir: Optional[Path], history_size: int):
        self.dev = dev
        self.cfg = cfg
        self.name = cfg.name
        self.ident = ident(cfg.name)
        self.table: str = cfg.table  # type: ignore[assignment]
        self.address: int = cfg.address  # type: ignore[assignment]
        self.n = register_count(cfg)
        self.datatype: str = cfg.datatype or "uint16"
        self.enc_dt = {"enum": "uint16", "sunspec_sf": "int16"}.get(self.datatype, self.datatype)
        self.nullable = bool(cfg.nullable) or self.datatype == "sunspec_sf"
        self.byte_order = cfg.byte_order or dev.cfg.byte_order
        self.word_order = cfg.word_order or dev.cfg.word_order
        self._scale = 1.0 if self.datatype in ("enum", "sunspec_sf") else cfg.scale
        self.offset = 0.0 if self.datatype == "enum" else cfg.offset
        self.access = cfg.access or ("rw" if self.table in ("coil", "holding") else "r")
        self.error: Optional[str] = None
        self.history: Deque[Tuple[float, float]] = deque(maxlen=history_size)
        discrete = self.datatype in ("bool", "bit", "enum")
        spec = cfg.behavior
        try:
            self.behavior: Optional[Behavior] = make_behavior(spec, base_dir, discrete, self._coerce_label)
        except Exception as e:
            raise ValueError(f"{dev.cfg.name}/{cfg.name}: {e}") from None
        self.behavior_type = (spec or {}).get("type") if self.behavior else None
        self.value: Any = None

    # ------------------------------------------------------------ conversion
    @property
    def scale(self) -> float:
        """``scale`` x 10^sf when ``scale_ref`` names a ``sunspec_sf`` point holding an implemented value."""
        ref = self.cfg.scale_ref
        if ref:
            sf_point = self.dev.by_name.get(ref) if hasattr(self.dev, "by_name") else None
            sf = sf_point.value if sf_point is not None else None
            if isinstance(sf, int) and not isinstance(sf, bool):
                return self._scale * (10 ** sf if sf >= 0 else 10.0 ** sf)
        return self._scale

    def _na_words(self) -> List[int]:
        if self.enc_dt in INT_TYPES:
            return dtypes.encode(NA_RAW[self.enc_dt], self.enc_dt, self.byte_order, self.word_order)
        return dtypes.encode(math.nan, self.enc_dt, self.byte_order, self.word_order)

    def _coerce_label(self, v: Any) -> Any:
        if isinstance(v, str) and self.cfg.labels:
            for k, label in self.cfg.labels.items():
                if label == v:
                    return k
        return v

    def _int_range(self) -> Tuple[int, int]:
        return dtypes._INT_RANGE[self.enc_dt]

    def normalize(self, v: Any, strict: bool = False) -> Any:
        """Engineering value of the point's type; ``strict`` raises instead of clamping/wrapping."""
        dt = self.datatype
        if dt in ("bool", "bit"):
            if isinstance(v, str):
                s = v.strip().lower()
                if s in ("1", "true", "on", "yes"):
                    return True
                if s in ("0", "false", "off", "no"):
                    return False
                raise ValueError(f"not a boolean: {v!r}")
            return bool(v)
        if dt == "string":
            s = "" if v is None else str(v)
            if len(s.encode("ascii", errors="replace")) > 2 * self.n:
                if strict:
                    raise ValueError(f"string longer than {2 * self.n} characters")
                s = s[: 2 * self.n]
            return s
        if v is None and self.nullable:
            return None  # "not implemented"
        v = self._coerce_label(v)
        if isinstance(v, str):
            v = float(v)
        exact = self._exact(v)
        v = int(v) if exact else float(v)
        if not exact and not math.isfinite(v):
            raise ValueError(f"non-finite value {v}")
        lo, hi = self.cfg.min, self.cfg.max
        if strict and ((lo is not None and v < lo) or (hi is not None and v > hi)):
            raise ValueError(f"{v} outside [{lo}, {hi}]")
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        if self.enc_dt in INT_TYPES:
            rlo, rhi = self._int_range()
            raw = self._raw(v)
            if not rlo <= raw <= rhi:
                if strict:
                    raise ValueError(f"{v} does not fit {self.enc_dt} (scale {self.scale})")
                if self.behavior_type in WRAPPING_BEHAVIORS:
                    raw = rlo + (raw - rlo) % (rhi - rlo + 1)
                else:
                    raw = max(rlo, min(rhi, raw))
                v = raw if exact else raw * self.scale + self.offset
            if dt == "enum":
                v = int(raw)
        return v

    def _exact(self, v: Any) -> bool:
        """Integer value on an unscaled integer type: kept exact (64-bit values exceed float precision)."""
        return (isinstance(v, int) and self.enc_dt in INT_TYPES and self.scale == 1 and self.offset == 0)

    def _raw(self, v: Any) -> int:
        return int(v) if self._exact(v) else round((float(v) - self.offset) / self.scale)

    def to_words(self, v: Any) -> List[int]:
        if v is None and self.nullable:
            return self._na_words()
        if self.enc_dt in INT_TYPES:
            raw = self._raw(v)
            return dtypes.encode(raw, self.enc_dt, self.byte_order, self.word_order)
        return dtypes.encode(v, self.enc_dt, self.byte_order, self.word_order, self.scale, self.offset,
                             count=self.n)

    def from_words(self, words: List[int]) -> Any:
        if self.datatype == "bit":
            return dtypes.get_bit(words[0], self.cfg.bit)  # type: ignore[arg-type]
        if self.datatype == "bool":
            return bool(words[0])
        if self.nullable:
            raw = dtypes.decode(words, self.enc_dt, self.byte_order, self.word_order, count=self.n)
            if (raw == NA_RAW[self.enc_dt]) if self.enc_dt in INT_TYPES else (raw != raw):
                return None  # "not implemented"
        v = dtypes.decode(words, self.enc_dt, self.byte_order, self.word_order, self.scale, self.offset,
                          count=self.n)
        return int(v) if self.datatype in ("enum", "sunspec_sf") else v

    # --------------------------------------------------------------- store
    @property
    def store(self) -> UnitStore:
        return self.dev.store

    def raw(self) -> List[int]:
        return self.store.get(self.table, self.address, self.n)

    def read(self) -> Any:
        """Value as a master sees it (decoded from the register image)."""
        return self.from_words(self.raw())

    def sync(self) -> None:
        """Write ``value`` into the register image (read-modify-write for ``bit`` points)."""
        if self.datatype == "bit":
            reg = self.store.get(self.table, self.address)[0]
            self.store.set(self.table, self.address, [dtypes.set_bit(reg, self.cfg.bit, bool(self.value))])
        else:
            self.store.set(self.table, self.address, self.to_words(self.value))

    def set_value(self, v: Any, strict: bool = False) -> None:
        self.value = self.normalize(v, strict)
        self.sync()

    def json_value(self) -> Any:
        v = self.read()
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return v

    def status(self) -> Dict[str, Any]:
        return {"value": self.json_value(), "raw": self.raw(), "error": self.error}

    def info(self) -> Dict[str, Any]:
        c = self.cfg
        return {"name": c.name, "description": c.description, "table": self.table, "address": self.address,
                "registers": self.n, "datatype": self.datatype, "count": c.count, "bit": c.bit,
                "byte_order": self.byte_order, "word_order": self.word_order, "scale": c.scale,
                "scale_ref": c.scale_ref, "nullable": self.nullable,
                "offset": c.offset, "units": c.units, "access": self.access, "min": c.min, "max": c.max,
                "labels": c.labels, "behavior": self.behavior_type, **self.status()}


DEFAULT_VENDOR_NAME = "SmartEdge"
DEFAULT_PRODUCT_CODE = "SME-MODBUS-SIM"
_IDENT_OBJECTS = ("vendor_name", "product_code", "revision", "vendor_url", "product_name", "model_name",
                  "user_application_name")  # object ids 0x00..0x06


def identification_objects(cfg: DeviceConfig) -> Dict[int, str]:
    """FC2B/0E objects of a device; the three mandatory basic objects always exist.

    Template ``identification`` defaults are merged under the device's own fields (device values win)."""
    own = {"identification": cfg.identification.model_dump(exclude_none=True)} if cfg.identification else {}
    merged = tpl.registry.expand_extras(cfg.name, cfg.template, cfg.params or {}, own).get("identification")
    ident = IdentificationConfig.model_validate(merged) if merged else None
    out: Dict[int, str] = {}
    for oid, field in enumerate(_IDENT_OBJECTS):
        v = getattr(ident, field) if ident is not None else None
        if v is not None:
            out[oid] = v
    out.setdefault(0x00, DEFAULT_VENDOR_NAME)
    out.setdefault(0x01, DEFAULT_PRODUCT_CODE)
    out.setdefault(0x02, __version__)
    return out


class DeviceRuntime:
    def __init__(self, engine: "Engine", cfg: DeviceConfig, base_dir: Optional[Path], config: SimConfig):
        self.cfg = cfg
        self.name = cfg.name
        self.listeners = config.device_listeners(cfg)
        self.store = UnitStore(cfg.unit_id, cfg.undefined_address)
        hs = config.simulation.history_size
        self.points: List[PointRuntime] = [PointRuntime(self, p, base_dir, hs) for p in expand_device(cfg)]
        self.by_name = {p.name: p for p in self.points}
        self.by_table: Dict[str, List[PointRuntime]] = {}
        for p in self.points:
            self.by_table.setdefault(p.table, []).append(p)
        # define ranges; a register shared by bit points is writable when any of them is rw
        for p in self.points:
            if p.datatype == "bit":
                continue
            self.store.define(p.table, p.address, p.n,
                              writable=p.access == "rw" and p.table in ("coil", "holding"))
        bit_regs: Dict[Tuple[str, int], bool] = {}
        for p in self.points:
            if p.datatype == "bit":
                k = (p.table, p.address)
                bit_regs[k] = bit_regs.get(k, False) or (p.access == "rw" and p.table == "holding")
        for (table, addr), writable in bit_regs.items():
            self.store.define(table, addr, 1, writable=writable)
        # sunspec_sf points first: points with scale_ref encode with the sf value
        for p in sorted(self.points, key=lambda q: q.datatype != "sunspec_sf"):
            default = "" if p.datatype == "string" else (None if p.nullable else 0)
            try:
                p.set_value(p.cfg.value if p.cfg.value is not None else default)
            except Exception as e:
                raise ValueError(f"{cfg.name}/{p.name}: initial value {p.cfg.value!r}: {e}") from None
        self.store.identification = identification_objects(cfg)
        self.store.add_write_validator(self._validate_write)
        self.store.on_write(lambda table, address, count: engine._on_protocol_write(self, table, address, count))

    def overlapping(self, table: str, start: int, end: int) -> List[PointRuntime]:
        return [p for p in self.by_table.get(table, []) if p.address < end and p.address + p.n > start]

    def _validate_write(self, table: str, address: int, values: list) -> None:
        end = address + len(values)
        for p in self.overlapping(table, address, end):
            if p.address < address or p.address + p.n > end:
                raise ModbusError(ExceptionCode.ILLEGAL_DATA_ADDRESS,
                                  f"partial write of {p.name} ({table} {p.address}..{p.address + p.n - 1})")
            words = values[p.address - address: p.address - address + p.n]
            if p.datatype == "bit":
                old = self.store.get(table, p.address)[0]
                if p.access != "rw" and dtypes.get_bit(old, p.cfg.bit) != dtypes.get_bit(words[0], p.cfg.bit):
                    raise ModbusError(ExceptionCode.ILLEGAL_DATA_ADDRESS, f"{p.name} is read-only")
                continue
            if p.access != "rw":
                raise ModbusError(ExceptionCode.ILLEGAL_DATA_ADDRESS, f"{p.name} is read-only")
            if p.datatype in ("bool", "string"):
                continue
            try:
                v = p.from_words(words)
            except Exception as e:
                raise ModbusError(ExceptionCode.ILLEGAL_DATA_VALUE, f"{p.name}: {e}") from None
            lo, hi = p.cfg.min, p.cfg.max
            if (isinstance(v, float) and not math.isfinite(v)) or (lo is not None and v < lo) or \
                    (hi is not None and v > hi):
                raise ModbusError(ExceptionCode.ILLEGAL_DATA_VALUE, f"{p.name}: {v} outside [{lo}, {hi}]")

    def info(self) -> Dict[str, Any]:
        c = self.cfg
        return {"name": c.name, "description": c.description, "unit_id": c.unit_id, "template": c.template,
                "listeners": self.listeners, "byte_order": c.byte_order, "word_order": c.word_order,
                "undefined_address": c.undefined_address, "points": [p.info() for p in self.points]}


class ListenerStats:
    def __init__(self) -> None:
        self.peers: set = set()
        self.connections = 0
        self.rx = 0
        self.tx = 0
        self.dropped = 0
        self.requests: Dict[str, int] = {}
        self.exceptions: Dict[str, int] = {}

    def add(self, ev: TraceEvent) -> None:
        if ev.peer not in self.peers:
            self.peers.add(ev.peer)
            self.connections += 1
        if ev.direction == "rx":
            self.rx += 1
            if ev.error:
                self.dropped += 1
            elif ev.function_code is not None:
                k = f"{ev.function_code:02X}"
                self.requests[k] = self.requests.get(k, 0) + 1
        else:
            self.tx += 1
            if ev.exception_code is not None:
                k = f"{ev.exception_code:02X}"
                self.exceptions[k] = self.exceptions.get(k, 0) + 1

    def as_dict(self) -> Dict[str, Any]:
        return {"connections": self.connections, "rx": self.rx, "tx": self.tx, "dropped": self.dropped,
                "requests": dict(self.requests), "exceptions": dict(self.exceptions)}


class Engine(BaseEngine):
    """Owns the devices, the Modbus listeners and the tick loop. All on one asyncio loop."""

    logger_name = "sme_modbus_sim.engine"

    event_size = EVENT_SIZE
    queue_size = QUEUE_SIZE
    stats_interval = STATS_INTERVAL  # seconds between {"type": "stats"} pushes on /ws

    def __init__(self, config: SimConfig, base_dir: Optional[Path | str] = None, *,
                 config_text: Optional[str] = None, config_path: Optional[Path | str] = None,
                 listener_overrides: Optional[Dict[str, Dict[str, Any]]] = None):
        """``listener_overrides`` = {listener id: {"host": ..., "port": ...}} wins over the config
        (also after reloads); port 0 binds an ephemeral port (tests)."""
        self.listener_overrides = listener_overrides or {}
        self.sim_epoch = time.time()
        self.trace_buf: Deque[Dict[str, Any]] = deque(maxlen=TRACE_SIZE)
        self._trace_pending: List[Dict[str, Any]] = []
        self.stats: Dict[str, ListenerStats] = {}
        self.servers: Dict[str, Any] = {}
        self.devices: Dict[str, DeviceRuntime] = {}
        self._last: Dict[Tuple[str, str], Any] = {}
        self.faults = FaultManager(self.publish)  # runtime fault rules (design 5.4), survive reloads
        super().__init__(config, config_text=config_text, config_path=config_path,
                         base_dir=base_dir)  # base_dir defaults to the config file's directory

    # ------------------------------------------------------------- building
    def _build(self, config: SimConfig) -> Dict[str, DeviceRuntime]:
        return {d.name: DeviceRuntime(self, d, self.base_dir, config) for d in config.devices}

    def _install(self, state: Dict[str, DeviceRuntime]) -> None:
        self.state = self.devices = state
        self._last = {}

    def dump_config(self, config: SimConfig) -> str:
        return dump_yaml(config)

    def _listener_addr(self, lc) -> Tuple[str, int]:
        o = self.listener_overrides.get(lc.id, {})
        return o.get("host", lc.host), o.get("port", lc.port)

    def _make_servers(self, config: SimConfig, devices: Dict[str, DeviceRuntime]) -> Dict[str, Any]:
        servers: Dict[str, Any] = {}
        for lc in config.listeners:
            units = {d.cfg.unit_id: d.store for d in devices.values() if lc.id in d.listeners}
            host, port = self._listener_addr(lc)
            self.stats.setdefault(lc.id, ListenerStats())
            if lc.type in SERIAL_TYPES:
                servers[lc.id] = ModbusSerialServer(
                    str(port), lc.baudrate or 9600, lc.bytesize or 8, lc.parity or "E", lc.stopbits or 1,
                    resolver=units.get, unit_id_mode=lc.unit_id_mode, default_unit=lc.default_unit,
                    trace=self._on_trace, listener_id=lc.id, all_units=lambda u=units: list(u.values()),
                    strict_timing=bool(lc.strict_timing), link=lc.link,
                    framer=lc.type, char_timeout=lc.char_timeout or 1.0)
                continue
            servers[lc.id] = ModbusTcpServer(
                host, port, framer=lc.type, resolver=units.get, unit_id_mode=lc.unit_id_mode,
                default_unit=lc.default_unit, trace=self._on_trace, listener_id=lc.id,
                unknown_unit=lc.unknown_unit, all_units=lambda u=units: list(u.values()))
            self.stats.setdefault(lc.id, ListenerStats())
        for srv in servers.values():
            srv.faults = self.faults
        return servers

    async def _start_servers(self, servers: Dict[str, Any]) -> None:
        """Start all listeners. TCP bind errors abort (and roll back); a serial port that cannot be
        opened only marks that listener ``error`` so the other listeners keep serving."""
        started = []
        try:
            for lid, s in servers.items():
                try:
                    await s.start()
                except OSError as e:
                    raise ValueError(f"listener {lid!r}: cannot bind {s.host}:{s.port}: {e.strerror or e}") from None
                started.append(s)
        except Exception:
            for s in started:
                await s.stop()
            raise

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.servers = self._make_servers(self.config, self.devices)
        await self._start_servers(self.servers)
        self._log_listeners(self.servers)
        await super().start()

    def _log_listeners(self, servers: Dict[str, Any]) -> None:
        for lid, s in servers.items():
            if s.error:
                self.log_event("error", f"listener {lid} ({s.framer}): {s.error}")
            else:
                self.log_event("info", f"listener {lid} ({s.framer}) on {s.endpoint}")

    async def stop(self) -> None:
        jobs = getattr(self, "scan_jobs", None)  # set by web.create_app; absent in engine-only use
        if jobs is not None:
            await jobs.stop()
        await super().stop()
        for s in self.servers.values():
            await s.stop()
        self.servers = {}

    async def _apply_diff(self, old_config: SimConfig, new_config: SimConfig,
                          new_state: Dict[str, DeviceRuntime]) -> None:
        """Rebind the listeners for the new devices (the old ones are stopped first so ports can be reused).

        A TCP bind error raises; ``_rollback`` then restarts the previous listeners."""
        old_servers = self.servers
        for s in old_servers.values():
            await s.stop()
        new_servers = self._make_servers(new_config, new_state)
        if self._task is not None or old_servers:
            await self._start_servers(new_servers)
        self._install(new_state)
        self.servers = new_servers
        self.stats = {lid: self.stats.get(lid, ListenerStats()) for lid in new_servers}
        for lid, s in new_servers.items():
            if s.error:
                self.log_event("error", f"listener {lid} ({s.framer}): {s.error}")

    async def _rollback(self, old_config: SimConfig, old_state: Dict[str, DeviceRuntime],
                        error: BaseException) -> None:
        self._install(old_state)
        self.servers = self._make_servers(old_config, old_state)
        await self._start_servers(self.servers)

    # ---------------------------------------------------------------- trace
    def _on_trace(self, ev: TraceEvent) -> None:
        d = asdict(ev)
        self.trace_buf.append(d)
        self._trace_pending.append(d)
        self.stats.setdefault(ev.listener_id, ListenerStats()).add(ev)
        self.wake()

    def trace(self, limit: int = 200, unit: Optional[int] = None, fc: Optional[int] = None,
              listener: Optional[str] = None) -> List[Dict[str, Any]]:
        out = [e for e in self.trace_buf
               if (unit is None or e["unit_id"] == unit) and (fc is None or e["function_code"] == fc)
               and (listener is None or e["listener_id"] == listener)]
        return out[-max(0, limit):] if limit else []

    def stats_snapshot(self) -> Dict[str, Any]:
        out = {}
        for lc in self.config.listeners:
            s = self.servers.get(lc.id)
            st = self.stats.get(lc.id, ListenerStats()).as_dict()
            host, port = self._listener_addr(lc)
            serial = lc.type in SERIAL_TYPES
            if serial and s is not None:
                port = s.display_port
            out[lc.id] = {"type": lc.type, "host": None if serial else host, "port": port,
                          "device": getattr(s, "device", None) if serial else None,
                          "bound_port": s.bound_port if s else None,
                          "clients": s.client_count if s else 0,
                          "status": s.status if s else "stopped", "error": s.error if s else None, **st}
        return out

    # ------------------------------------------------------- protocol write
    def _on_protocol_write(self, dev: DeviceRuntime, table: str, address: int, count: int) -> None:
        for p in dev.overlapping(table, address, address + count):
            v = p.read()
            if p.datatype == "bit" and bool(v) == bool(p.value):
                continue
            p.value = v
            p.error = None
            self.log_event("write", f"{dev.name}/{p.name} <- {v!r} (Modbus {table} {p.address})")
        self.wake()

    # ----------------------------------------------------------------- API
    def _point(self, device: str, point: str) -> PointRuntime:
        dev = self.devices.get(device)
        if dev is None:
            raise KeyError(f"device {device!r} not found")
        p = dev.by_name.get(point)
        if p is None:
            raise KeyError(f"point {device}/{point} not found")
        return p

    def set_point(self, device: str, point: str, value: Any) -> Any:
        """Engine-side write (UI / REST): bypasses ``access``; checks min/max and the datatype range."""
        p = self._point(device, point)
        p.set_value(value, strict=True)
        p.error = None
        self.log_event("write", f"{device}/{point} <- {p.read()!r} (UI)")
        self.wake()
        return p.read()

    def history(self, device: str, point: str) -> List[Tuple[float, float]]:
        return list(self._point(device, point).history)

    @property
    def sim_hour(self) -> float:
        dt = datetime.fromtimestamp(self.sim_epoch + self.sim_time)
        return dt.hour + dt.minute / 60 + dt.second / 3600

    def snapshot(self) -> Dict[str, Any]:
        stats = self.stats_snapshot()
        return {"version": __version__, "running": self.running, "time_scale": self.time_scale,
                "tick_seconds": self.tick_seconds, "sim_time": self.sim_time, "sim_hour": self.sim_hour,
                "listeners": [{"id": l.id, "type": l.type, **{k: v for k, v in stats[l.id].items()
                                                              if k in ("host", "port", "device", "bound_port", "clients", "status", "error")}}
                              for l in self.config.listeners],
                "devices": [d.info() for d in self.devices.values()],
                "events": list(self.events), "stats": stats}


    # ------------------------------------------------------------ tick loop
    def _pv(self, device: str, point: str) -> Any:
        return self._point(device, point).value

    def _step(self, dt: float) -> None:
        base = {"t": self.sim_time, "dt": dt, "hour": self.sim_hour, "pv": self._pv}
        for dev in self.devices.values():
            if self.faults.is_frozen(dev.cfg.unit_id, dev.listeners):
                continue  # fault "freeze": values hold, protocol still served
            names = dict(base)
            names.update({p.ident: p.value for p in dev.points})
            for p in dev.points:
                if p.behavior is None:
                    continue
                try:
                    v = p.behavior.step(names, names[p.ident], dt)
                    if v is None:
                        continue
                    p.set_value(v)
                    names[p.ident] = p.value
                    p.error = None
                except Exception as e:  # keep running, surface the error in the UI
                    msg = f"{type(e).__name__}: {e}"
                    if p.error != msg:
                        p.error = msg
                        self.log_event("error", f"{dev.name}/{p.name}: {msg}")

    def _scan_changes(self) -> None:
        now = time.time()
        changes = []
        for dev in self.devices.values():
            for p in dev.points:
                st = p.status()
                k = (dev.name, p.name)
                if self._last.get(k) != st:
                    self._last[k] = st
                    changes.append({"d": dev.name, "k": p.name, **st})
                    v = st["value"]
                    if isinstance(v, (int, float)) and v is not None:
                        p.history.append((now, float(v)))
        if changes:
            self.publish({"type": "values", "t": now, "sim_time": self.sim_time, "sim_hour": self.sim_hour,
                          "changes": changes})
        if self._trace_pending:
            events, self._trace_pending = self._trace_pending, []
            self.publish({"type": "trace", "events": events})

    def _on_tick(self) -> None:
        self._scan_changes()
