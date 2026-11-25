"""Configuration model and file loading (YAML / JSON).

Skeleton (``_Model``, ``SimulationConfig``, loaders) follows sme-bac-sim ``sme_bacnet_sim/config.py`` @ ebd27ec;
listener / device / point models are Modbus specific (design §2.4, §5.1).
Addresses are 0-based protocol addresses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TABLES = ("coil", "discrete", "input", "holding")
BIT_TABLES = ("coil", "discrete")
REGISTER_TABLES = ("input", "holding")
DATATYPES = ("bool", "bit", "uint16", "int16", "uint32", "int32", "float32",
             "uint64", "int64", "float64", "string", "enum")
REGISTER_COUNT = {"bit": 1, "uint16": 1, "int16": 1, "enum": 1,
                  "uint32": 2, "int32": 2, "float32": 2,
                  "uint64": 4, "int64": 4, "float64": 4}
MAX_ADDRESS = 65535

Table = Literal["coil", "discrete", "input", "holding"]
Datatype = Literal["bool", "bit", "uint16", "int16", "uint32", "int32", "float32",
                   "uint64", "int64", "float64", "string", "enum"]
Order = Literal["big", "little"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SimulationConfig(_Model):
    tick_seconds: float = Field(default=1.0, gt=0.05, le=60)
    time_scale: float = Field(default=1.0, gt=0, le=3600)  # sim seconds per real second
    history_size: int = Field(default=600, ge=10, le=100000)
    running: bool = True


class ListenerConfig(_Model):
    """A Modbus server endpoint. One listener serves many unit ids (design §12 Q4)."""

    id: str = Field(min_length=1)
    type: Literal["tcp", "rtu_over_tcp"] = "tcp"
    host: str = "0.0.0.0"
    port: int = Field(default=5020, ge=1, le=65535)
    unit_id_mode: Literal["strict", "any"] = "strict"
    unknown_unit: Literal["ignore", "exception"] = "ignore"
    default_unit: Optional[int] = Field(default=None, ge=1, le=247)

    @model_validator(mode="before")
    @classmethod
    def _reserved_types(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("type") == "rtu":
            raise ValueError(f"listener {data.get('id', '?')!r}: type 'rtu' (serial) is not supported in 0.1.0 "
                             "(planned for 0.2.0; use rtu_over_tcp with a serial gateway / virtual COM)")
        return data


class IdentificationConfig(_Model):
    """Device identification objects (FC 43 / 14). Stored only in 0.1.0; served from 0.2.0."""

    vendor_name: Optional[str] = None
    product_code: Optional[str] = None
    revision: Optional[str] = None
    vendor_url: Optional[str] = None
    product_name: Optional[str] = None
    model_name: Optional[str] = None


class PointConfig(_Model):
    """A value mapped onto a contiguous address range of one table.

    ``table`` may be omitted only when the point overrides a template point of the same name.
    ``address`` may be omitted: template expansion allocates the next free address.
    """

    name: str = Field(min_length=1)
    description: Optional[str] = None
    table: Optional[Table] = None
    address: Optional[int] = Field(default=None, ge=0, le=MAX_ADDRESS)
    datatype: Optional[Datatype] = None
    count: Optional[int] = Field(default=None, ge=1, le=123)  # string: registers (2 chars each)
    bit: Optional[int] = Field(default=None, ge=0, le=15)
    byte_order: Optional[Order] = None  # None = device default
    word_order: Optional[Order] = None
    scale: float = 1.0
    offset: float = 0.0
    units: Optional[str] = None
    access: Optional[Literal["r", "rw"]] = None
    value: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    labels: Optional[Dict[int, str]] = None  # enum: raw value -> label
    behavior: Optional[Dict[str, Any]] = None

    @field_validator("labels", mode="before")
    @classmethod
    def _labels_are_text(cls, v: Any) -> Any:
        if isinstance(v, dict):
            for k, s in v.items():
                if isinstance(s, bool):
                    raise ValueError(f"label {k}: YAML read {s!r} as a boolean; quote it (e.g. \"ON\" / \"OFF\")")
        return v

    @field_validator("behavior")
    @classmethod
    def _behavior_type(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is not None and "type" not in v:
            raise ValueError("behavior needs a 'type'")
        return v

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("table") in TABLES:
            data = dict(data)
            bits = data["table"] in BIT_TABLES
            data.setdefault("datatype", "bool" if bits else "uint16")
            data.setdefault("access", "rw" if data["table"] in ("coil", "holding") else "r")
        return data

    @model_validator(mode="after")
    def _check(self) -> "PointConfig":
        if self.scale == 0:
            raise ValueError(f"point {self.name!r}: scale must not be 0")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"point {self.name!r}: min > max")
        if self.table is None:
            return self  # template override; checked after merge
        dt, where = self.datatype, f"point {self.name!r}"
        if self.table in BIT_TABLES and dt != "bool":
            raise ValueError(f"{where}: table {self.table} holds single bits; datatype must be bool, got {dt}")
        if self.table in REGISTER_TABLES and dt == "bool":
            raise ValueError(f"{where}: table {self.table} holds registers; use datatype bit (with bit: 0..15) "
                             "or a numeric type instead of bool")
        if self.table in ("discrete", "input") and self.access == "rw":
            raise ValueError(f"{where}: table {self.table} is read-only; access must be r")
        if dt == "bit" and self.bit is None:
            raise ValueError(f"{where}: datatype bit needs bit: 0..15")
        if dt != "bit" and self.bit is not None:
            raise ValueError(f"{where}: bit is only valid with datatype bit")
        if dt == "string" and self.count is None:
            raise ValueError(f"{where}: datatype string needs count (number of registers)")
        if dt != "string" and self.count is not None:
            raise ValueError(f"{where}: count is only valid with datatype string")
        if self.labels is not None and dt != "enum":
            raise ValueError(f"{where}: labels are only valid with datatype enum")
        return self


def register_count(p: PointConfig) -> int:
    """Addresses occupied: 1 for bool / bit / 16-bit types, 2 for 32-bit, 4 for 64-bit, ``count`` for string."""
    if p.datatype == "bool":
        return 1
    if p.datatype == "string":
        return int(p.count or 1)
    return REGISTER_COUNT[p.datatype or "uint16"]


def check_layout(device: str, points: List[PointConfig]) -> None:
    """Reject overlapping address ranges within each table (``bit`` points may share a register)."""
    for table in TABLES:
        ranges: List[tuple] = []  # (start, end_exclusive, name)
        bit_regs: Dict[int, Dict[int, str]] = {}
        for p in points:
            if p.table != table or p.address is None:
                continue
            n = register_count(p)
            if p.address + n - 1 > MAX_ADDRESS:
                raise ValueError(f"{device}/{p.name}: {table} {p.address}+{n} exceeds address {MAX_ADDRESS}")
            if p.datatype == "bit":
                bits = bit_regs.setdefault(p.address, {})
                if p.bit in bits:
                    raise ValueError(f"{device}/{p.name}: {table} {p.address} bit {p.bit} already used by "
                                     f"{bits[p.bit]!r}")
                bits[p.bit] = p.name
            else:
                ranges.append((p.address, p.address + n, p.name))
        for addr, bits in bit_regs.items():
            ranges_hit = [r for r in ranges if r[0] <= addr < r[1]]
            if ranges_hit:
                raise ValueError(f"{device}/{next(iter(bits.values()))}: {table} {addr} overlaps "
                                 f"{ranges_hit[0][2]!r}")
        ranges.sort()
        for a, b in zip(ranges, ranges[1:]):
            if b[0] < a[1]:
                raise ValueError(f"{device}/{b[2]}: {table} {b[0]}..{b[1] - 1} overlaps {a[2]!r} "
                                 f"({a[0]}..{a[1] - 1})")


class DeviceConfig(_Model):
    """One Modbus unit (slave). Can be attached to several listeners (same register image)."""

    name: str = Field(min_length=1)
    description: Optional[str] = None
    unit_id: int = Field(ge=1, le=247)
    listeners: Optional[List[str]] = None  # None = all listeners
    template: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    byte_order: Order = "big"
    word_order: Order = "big"
    undefined_address: Literal["exception", "zero"] = "exception"
    identification: Optional[IdentificationConfig] = None
    points: List[PointConfig] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)  # template points deleted by the user

    @model_validator(mode="after")
    def _check(self) -> "DeviceConfig":
        names = [p.name for p in self.points]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"device {self.name!r}: duplicate point names {dup}")
        if not self.template:
            missing = [p.name for p in self.points if p.table is None]
            if missing:
                raise ValueError(f"device {self.name!r}: points {missing} need a table")
        return self


def _default_listeners() -> List[ListenerConfig]:
    return [ListenerConfig(id="tcp1")]


class SimConfig(_Model):
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    listeners: List[ListenerConfig] = Field(default_factory=_default_listeners)
    devices: List[DeviceConfig] = Field(default_factory=list)

    def device_listeners(self, dev: DeviceConfig) -> List[str]:
        return list(dev.listeners) if dev.listeners is not None else [l.id for l in self.listeners]

    @model_validator(mode="after")
    def _check(self) -> "SimConfig":
        ids = [l.id for l in self.listeners]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicate listener id: {dup}")
        eps = [(l.host, l.port) for l in self.listeners]
        dup_ep = sorted({f"{h}:{p}" for h, p in eps if eps.count((h, p)) > 1})
        if dup_ep:
            raise ValueError(f"listeners share the same host:port: {dup_ep}")
        names = [d.name for d in self.devices]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate device name: {dup}")
        seen: Dict[tuple, str] = {}
        for d in self.devices:
            for lid in self.device_listeners(d):
                if lid not in ids:
                    raise ValueError(f"device {d.name!r}: unknown listener {lid!r} (known: {', '.join(ids)})")
                key = (lid, d.unit_id)
                if key in seen:
                    raise ValueError(f"listener {lid!r}: unit_id {d.unit_id} used by both {seen[key]!r} "
                                     f"and {d.name!r}")
                seen[key] = d.name
        for l in self.listeners:
            if l.default_unit is not None and (l.id, l.default_unit) not in seen:
                raise ValueError(f"listener {l.id!r}: default_unit {l.default_unit} is not a device on it")
        from .templates import expand_device  # lazy: templates imports this module

        for d in self.devices:  # template / override / address layout errors surface at load time
            expand_device(d)
        return self


def parse_text(text: str, fmt: Optional[str] = None) -> SimConfig:
    """Parse YAML or JSON text (YAML is a superset of JSON)."""
    data = json.loads(text) if fmt == "json" else yaml.safe_load(text)
    return SimConfig.model_validate(data or {})


def load_file(path: Union[str, Path]) -> SimConfig:
    p = Path(path)
    return parse_text(p.read_text(encoding="utf-8"), "json" if p.suffix.lower() == ".json" else None)


def dump_yaml(cfg: Union[SimConfig, BaseModel, dict]) -> str:
    data = cfg.model_dump(exclude_none=True, exclude_defaults=False) if isinstance(cfg, BaseModel) else cfg
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
