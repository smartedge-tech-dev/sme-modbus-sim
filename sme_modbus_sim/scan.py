"""Read-only scan / record of a real Modbus device (design ai/design/20261125-0003-scan-record-design.md).

* ``scan``    discovers readable addresses in user-given ranges (block reads, bisect on errors), samples them
              a few times and guesses datatypes (``suggest_*``: pure heuristics, design §4 / S3)
* ``to_template`` turns a scan result plus the user's edits into a user-template dict (``template_yaml``
              renders it with the guesses as YAML comments)
* ``record``  polls a point list and writes a replay CSV (ISO timestamp + one column per point)
* ``ScanJobs`` runs one scan / record job at a time in the background for the web UI (WS event ``scan``)

Only ``protocol.client.ModbusClient`` talks to the target, and it can only send FC 01/02/03/04 and 2B/0E.

Replay CSV and failed reads: a failed read writes an empty cell. ``sme_sim_common`` ``ReplayTable`` treats an
empty cell as "hold the previous value" -- but an empty cell in a column's FIRST data row is an error
("column has no value"), so a record whose first poll of some point failed is reported in ``warnings`` and
needs that cell filled (or the row removed) before it can be replayed.
"""
from __future__ import annotations

import asyncio
import csv
import math
import re
import struct
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import yaml

from .config import BIT_TABLES, TABLES, PointConfig, check_layout, register_count
from .protocol.client import DEVICE_ID_OBJECTS, ClientError, ModbusClient, ResponseTimeout
from .protocol.datatypes import decode, get_bit
from .protocol.errors import ModbusError

READ_ONLY_NOTE = "read-only: FC 01/02/03/04 and 2B/0E only"
REGISTER_BLOCK = 32
BIT_BLOCK = 64
NAME_PREFIX = {"holding": "hr", "input": "ir", "coil": "coil", "discrete": "di"}
EXC_ILLEGAL_FUNCTION = 0x01

Progress = Optional[Callable[[Dict[str, Any]], None]]


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- inputs

@dataclass
class ScanRange:
    table: str
    start: int
    count: int

    def as_dict(self) -> Dict[str, Any]:
        return {"table": self.table, "start": self.start, "count": self.count}


_RANGE_RE = re.compile(r"^\s*(coil|discrete|input|holding)\s*:\s*(\d+)\s*(?:-\s*(\d+))?\s*$")


def parse_range(v: Any) -> ScanRange:
    """``"holding:0-99"`` (inclusive), ``"coil:5"`` or ``{"table", "start", "count"}``."""
    if isinstance(v, ScanRange):
        r = v
    elif isinstance(v, str):
        m = _RANGE_RE.match(v)
        if not m:
            raise ValueError(f"bad range {v!r} (expected TABLE:START-END, e.g. holding:0-99)")
        start, end = int(m.group(2)), int(m.group(3) or m.group(2))
        if end < start:
            raise ValueError(f"bad range {v!r}: end < start")
        r = ScanRange(m.group(1), start, end - start + 1)
    elif isinstance(v, dict):
        try:
            r = ScanRange(str(v["table"]), int(v["start"]), int(v["count"]))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"bad range {v!r} (expected table / start / count)") from None
    else:
        raise ValueError(f"bad range {v!r}")
    if r.table not in TABLES:
        raise ValueError(f"bad range table {r.table!r} (one of {', '.join(TABLES)})")
    if r.count < 1 or r.start < 0 or r.start + r.count > 0x10000:
        raise ValueError(f"bad range {r.table}:{r.start}+{r.count} (addresses 0..65535)")
    return r


def parse_ranges(values: Iterable[Any]) -> List[ScanRange]:
    out = [parse_range(v) for v in values or []]
    if not out:
        raise ValueError("at least one address range is required (e.g. holding:0-99)")
    return out


TARGET_KEYS = ("type", "host", "port", "baudrate", "bytesize", "parity", "stopbits")


def make_client(target: Dict[str, Any], timeout: float = 1.0, rate: float = 10.0, **kw: Any) -> ModbusClient:
    """Client from a listener-style target ``{type, host, port, baudrate, bytesize, parity, stopbits}``."""
    if not isinstance(target, dict):
        raise ValueError("target must be an object {type, host, port, ...}")
    unknown = sorted(set(target) - set(TARGET_KEYS))
    if unknown:
        raise ValueError(f"unknown target key(s) {unknown} (allowed: {', '.join(TARGET_KEYS)})")
    kind = target.get("type", "tcp")
    if kind == "rtu":
        if not str(target.get("port") or "").strip():
            raise ValueError("target type rtu needs port: a serial device (e.g. COM5) or a pyserial URL")
        return ModbusClient("rtu", port=str(target["port"]).strip(), baudrate=target.get("baudrate") or 19200,
                            bytesize=target.get("bytesize") or 8, parity=target.get("parity") or "E",
                            stopbits=target.get("stopbits"), timeout=timeout, rate=rate, **kw)
    host = str(target.get("host") or "").strip()
    if not host:
        raise ValueError(f"target type {kind} needs host")
    try:
        port = int(target.get("port", 502))
    except (TypeError, ValueError):
        raise ValueError("target port must be a TCP port 1..65535") from None
    if not 1 <= port <= 65535:
        raise ValueError("target port must be a TCP port 1..65535")
    return ModbusClient(kind, host=host, port=port, timeout=timeout, rate=rate, **kw)


def check_unit(unit: Any) -> int:
    try:
        u = int(unit)
    except (TypeError, ValueError):
        raise ValueError("unit must be an integer 0..255") from None
    if not 0 <= u <= 255:
        raise ValueError("unit must be an integer 0..255")
    return u


# ---------------------------------------------------------------- plan (no connection)

def _block(table: str, block_size: Optional[int]) -> int:
    return int(block_size or (BIT_BLOCK if table in BIT_TABLES else REGISTER_BLOCK))


def scan_plan(target: str, unit: int, ranges: List[ScanRange], rate: float, samples: int = 3,
              sample_interval: float = 1.0, block_size: Optional[int] = None, timeout: float = 1.0) -> Dict[str, Any]:
    """Estimated request count / duration of a scan, assuming no holes (holes add bisect requests)."""
    blocks = sum(math.ceil(r.count / _block(r.table, block_size)) for r in ranges)
    requests = blocks * max(samples, 1) + 1  # + Read Device Identification
    duration = requests / rate + max(samples - 1, 0) * sample_interval
    return {"kind": "scan", "target": target, "unit": unit, "ranges": [r.as_dict() for r in ranges],
            "addresses": sum(r.count for r in ranges), "requests": requests, "duration_s": round(duration, 1),
            "rate": rate, "timeout": timeout, "samples": samples, "read_only": READ_ONLY_NOTE,
            "note": "estimate assumes every block is readable; holes add bisect requests"}


# ---------------------------------------------------------------- scan

async def _identify(client: ModbusClient, unit: int) -> Optional[Dict[str, str]]:
    try:
        objs = await client.read_device_identification(unit, 0x02)
    except (ModbusError, ClientError):
        try:
            objs = await client.read_device_identification(unit, 0x01)
        except (ModbusError, ClientError):
            return None
    out = {DEVICE_ID_OBJECTS[i]: v for i, v in sorted(objs.items()) if i in DEVICE_ID_OBJECTS and v}
    return out or None


async def scan(client: ModbusClient, unit: int, ranges: List[ScanRange], *, samples: int = 3,
               sample_interval: float = 1.0, block_size: Optional[int] = None, max_timeouts: int = 3,
               identify: bool = True, progress: Progress = None,
               cancel: Optional[asyncio.Event] = None) -> Dict[str, Any]:
    """Discover readable addresses, sample them ``samples`` times and suggest datatypes.

    Block reads; a block that fails (exception or timeout) is bisected down to single addresses. Exception 01
    (illegal function) skips the rest of that range. ``max_timeouts`` consecutive timeouts abort the scan with
    ``ResponseTimeout``. ``cancel`` set -> ``Cancelled``.
    """
    unit = check_unit(unit)
    t0 = time.monotonic()
    est = scan_plan(client.endpoint, unit, ranges, client.rate, samples, sample_interval, block_size)["requests"]
    state = {"timeouts": 0, "errors": 0}
    readable: Dict[str, Dict[int, List[int]]] = {t: {} for t in TABLES}

    def report(phase: str) -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if progress:
            progress({"phase": phase, "requests": client.requests, "estimate": max(est, client.requests),
                      "found": sum(len(v) for v in readable.values()), "errors": state["errors"]})

    async def try_read(table: str, start: int, count: int) -> Optional[List[int]]:
        report("discover")
        try:
            vals = await client.read(table, unit, start, count)
        except ResponseTimeout:
            state["timeouts"] += 1
            state["errors"] += 1
            if state["timeouts"] >= max_timeouts:
                raise
            return None
        except ModbusError as e:
            state["timeouts"] = 0
            state["errors"] += 1
            if e.exception_code == EXC_ILLEGAL_FUNCTION:
                raise
            return None
        state["timeouts"] = 0
        return vals

    async def probe(table: str, start: int, count: int) -> None:
        vals = await try_read(table, start, count)
        if vals is not None:
            for i, v in enumerate(vals):
                readable[table][start + i] = [v]
        elif count > 1:
            half = count // 2
            await probe(table, start, half)
            await probe(table, start + half, count - half)

    skipped: List[str] = []
    for r in ranges:
        size = _block(r.table, block_size)
        try:
            for start in range(r.start, r.start + r.count, size):
                await probe(r.table, start, min(size, r.start + r.count - start))
        except ModbusError:
            skipped.append(r.table)  # exception 01: table not supported by the target

    for _ in range(max(samples, 1) - 1):  # further samples of the readable runs
        if sample_interval > 0:
            if cancel is not None:
                try:
                    await asyncio.wait_for(cancel.wait(), sample_interval)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(sample_interval)
        for table, addrs in readable.items():
            for start, count in runs(sorted(addrs), _block(table, block_size)):
                report("sample")
                try:
                    vals = await client.read(table, unit, start, count)
                except ResponseTimeout:
                    state["errors"] += 1
                    state["timeouts"] += 1
                    if state["timeouts"] >= max_timeouts:
                        raise
                    continue
                except ModbusError:
                    state["errors"] += 1
                    continue
                state["timeouts"] = 0
                for i, v in enumerate(vals):
                    addrs[start + i].append(v)

    ident = None
    if identify:
        report("identify")
        ident = await _identify(client, unit)
    report("done")
    rows = [{"table": t, "address": a, "samples": readable[t][a]} for t in TABLES for a in sorted(readable[t])]
    suggestions = [s for t in TABLES for s in suggest(t, readable[t])]
    return {"target": client.endpoint, "unit": unit, "ranges": [r.as_dict() for r in ranges],
            "identification": ident, "readable": rows, "suggestions": suggestions,
            "skipped_tables": skipped, "requests": client.requests, "errors": state["errors"],
            "elapsed_s": round(time.monotonic() - t0, 2)}


def runs(addresses: List[int], max_len: int) -> List[Tuple[int, int]]:
    """Contiguous (start, count) runs of sorted addresses, each at most ``max_len`` long."""
    out: List[Tuple[int, int]] = []
    for a in addresses:
        if out and out[-1][0] + out[-1][1] == a and out[-1][1] < max_len:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((a, 1))
    return out


# ---------------------------------------------------------------- heuristics (pure)

FLOAT_MIN_NORMAL = 1.1754943508222875e-38
FLOAT_MAX_PLAUSIBLE = 1e7
FLOAT_MIN_PLAUSIBLE = 1e-6


def _f32(hi: int, lo: int) -> float:
    return struct.unpack(">f", struct.pack(">HH", hi & 0xFFFF, lo & 0xFFFF))[0]


def float_plausible(x: float) -> bool:
    """Finite, not denormal, and 0 or within 1e-6 <= |x| <= 1e7."""
    if not math.isfinite(x):
        return False
    if x == 0:
        return True
    return FLOAT_MIN_NORMAL <= abs(x) and FLOAT_MIN_PLAUSIBLE <= abs(x) <= FLOAT_MAX_PLAUSIBLE


def _smooth(values: List[float]) -> bool:
    """Successive changes small compared to the magnitude (a noisy low word looks random when misread)."""
    span = max(abs(v) for v in values) or 1.0
    return all(abs(b - a) <= 0.5 * span for a, b in zip(values, values[1:]))


def guess_float32(w0: List[int], w1: List[int]) -> Optional[Dict[str, Any]]:
    """Two consecutive registers' samples -> float32 guess (ABCD or CDAB) or None."""
    if all(a == 0 and b == 0 for a, b in zip(w0, w1)):
        return None
    cands = []
    for order, word_order in (("ABCD", "big"), ("CDAB", "little")):
        hi, lo = (w0, w1) if order == "ABCD" else (w1, w0)
        if len(set(hi)) > 1 and len(set(lo)) == 1 and lo[0] != 0:
            continue  # high word changes under a fixed non-zero low mantissa word: misaligned pair
        vals =[_f32(a, b) if order == "ABCD" else _f32(b, a) for a, b in zip(w0, w1)]
        if all(float_plausible(v) for v in vals) and any(v != 0 for v in vals) and _smooth(vals):
            cands.append((order, word_order, vals))
    if not cands:
        return None
    order, word_order, vals = cands[0]
    changing = len(set(vals)) > 1
    conf = 0.6 + (0.2 if changing else 0.0) + (0.1 if len(cands) == 1 else 0.0)
    shown = ', '.join(f'{v:.6g}' for v in vals)
    reason = (f"float32 {order}: plausible values {shown}"
              + (" (changing smoothly)" if changing else " (constant)")
              + ("" if len(cands) == 1 else f"; {cands[1][0]} also plausible"))
    code = "float32_" + ("smooth" if changing else "constant") + ("" if len(cands) == 1 else "_ambiguous")
    params = {"order": order, "values": shown}
    if len(cands) > 1:
        params["alt"] = cands[1][0]
    return {"datatype": "float32", "byte_order": "big", "word_order": word_order, "confidence": round(conf, 2),
            "reason": reason, "reason_code": code, "reason_params": params, "count": 2}


def guess_int32(w0: List[int], w1: List[int]) -> Optional[Dict[str, Any]]:
    """High word (first register, ABCD) constant 0x0000 / 0xFFFF while the low word changes -> int32 / uint32."""
    if len(set(w1)) < 2 or not all(h in (0x0000, 0xFFFF) for h in w0):
        return None
    signed = any(h == 0xFFFF for h in w0)
    dt = "int32" if signed else "uint32"
    conf = 0.7 if signed else 0.55
    return {"datatype": dt, "byte_order": "big", "word_order": "big", "confidence": conf, "count": 2,
            "reason": f"{dt} ABCD: high word constant 0x{w0[0]:04X}-ish, low word changes",
            "reason_code": "int32_high_word_const", "reason_params": {"datatype": dt, "high": f"0x{w0[0]:04X}"}}


def _printable(w: int) -> Tuple[bool, int]:
    hi, lo = w >> 8, w & 0xFF
    ok = all(b == 0 or 0x20 <= b < 0x7F for b in (hi, lo)) and 0x20 <= hi < 0x7F
    return ok, sum(1 for b in (hi, lo) if 0x20 <= b < 0x7F)


def guess_string(words: List[List[int]]) -> Optional[Dict[str, Any]]:
    """Consecutive constant registers of printable ASCII (>= 4 printable bytes) -> string, count = registers."""
    n, chars = 0, 0
    for samples in words:
        if len(set(samples)) != 1:
            break
        ok, c = _printable(samples[0])
        if not ok:
            break
        n, chars = n + 1, chars + c
        if samples[0] & 0xFF == 0:  # NUL terminator
            break
    if n < 2 or chars < 4:
        return None
    text = b"".join(struct.pack(">H", s[0]) for s in words[:n]).rstrip(b"\x00").decode("ascii")
    return {"datatype": "string", "byte_order": "big", "word_order": "big", "count": n,
            "confidence": round(min(0.9, 0.5 + 0.05 * chars), 2), "reason": f"printable ASCII {text!r}",
            "reason_code": "ascii_run", "reason_params": {"text": text}}


def guess_int16(samples: List[int]) -> Optional[Dict[str, Any]]:
    """All samples >= 0xF000 (just below 0x10000) -> small negative int16."""
    if samples and all(s >= 0xF000 for s in samples):
        vals = [s - 0x10000 for s in samples]
        return {"datatype": "int16", "byte_order": "big", "word_order": "big", "count": 1, "confidence": 0.6,
                "reason": f"values near 0xFFFF read as int16 {vals}",
                "reason_code": "int16_negative", "reason_params": {"values": ", ".join(map(str, vals))}}
    return None


def guess_bool(samples: List[int]) -> Dict[str, Any]:
    changing = len(set(samples)) > 1
    return {"datatype": "bool", "byte_order": "big", "word_order": "big", "count": 1,
            "confidence": 0.8 if changing else 0.9, "reason": "single bit" + (" (changing)" if changing else ""),
            "reason_code": "bool_bits_changing" if changing else "bool_bits", "reason_params": {}}


# Every ``reason_code`` the heuristics can emit (the web UI localises ``scan.reason.<code>`` with
# ``reason_params``; ``reason`` stays the English text for the CLI YAML comments and API clients).
REASON_CODES = ("float32_smooth", "float32_constant", "float32_smooth_ambiguous", "float32_constant_ambiguous",
                "int32_high_word_const", "ascii_run", "int16_negative", "bool_bits", "bool_bits_changing",
                "default")


def suggest(table: str, samples: Dict[int, List[int]]) -> List[Dict[str, Any]]:
    """Datatype suggestions for one table: ``{address, table, datatype, byte_order, word_order, count, name,
    confidence, reason, reason_code, reason_params, value}`` (``value`` decoded from the last sample). Addresses consumed by a multi-register
    guess are not suggested again."""
    out: List[Dict[str, Any]] = []
    addrs = sorted(samples)
    used: set = set()
    for a in addrs:
        if a in used:
            continue
        s = samples[a]
        if table in BIT_TABLES:
            g = guess_bool(s)
        else:
            run = []
            b = a
            while b in samples and b not in used and len(run) < 64:
                run.append(samples[b])
                b += 1
            pair = len(run) >= 2 and len(run[0]) == len(run[1])
            g = (guess_string(run)
                 or (guess_float32(run[0], run[1]) if pair else None)
                 or (guess_int32(run[0], run[1]) if pair else None)
                 or guess_int16(s)
                 or {"datatype": "uint16", "byte_order": "big", "word_order": "big", "count": 1,
                     "confidence": 0.5, "reason": "default (no stronger pattern)", "reason_code": "default",
                     "reason_params": {}})
        n = g["count"]
        used.update(range(a, a + n))
        words = [samples[a + i][-1] for i in range(n)]
        out.append({"table": table, "address": a, "name": f"{NAME_PREFIX[table]}_{a}", **g,
                    "value": _decode_value(g["datatype"], words, g["byte_order"], g["word_order"], n)})
    return out


def _decode_value(datatype: str, words: List[int], byte_order: str = "big", word_order: str = "big",
                  count: int = 1, scale: float = 1.0, offset: float = 0.0) -> Any:
    if datatype == "bool":
        return 1 if words[0] else 0
    if datatype == "enum":
        datatype = "uint16"
    elif datatype == "sunspec_sf":
        datatype = "int16"
    v = decode(words, datatype, byte_order, word_order, scale, offset, count if datatype == "string" else 1)
    if isinstance(v, float):
        v = float(f"{v:.7g}")
    return v


# ---------------------------------------------------------------- template

EDIT_KEYS = ("table", "address", "keep", "name", "datatype", "count", "byte_order", "word_order", "scale",
             "offset", "units", "description")


def _merge_edits(result: Dict[str, Any], edits: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows = {(s["table"], s["address"]): dict(s) for s in result.get("suggestions", [])}
    for e in edits or []:
        if not isinstance(e, dict):
            raise ValueError("each edit is an object with table / address")
        unknown = sorted(set(e) - set(EDIT_KEYS))
        if unknown:
            raise ValueError(f"unknown edit key(s) {unknown} (allowed: {', '.join(EDIT_KEYS)})")
        try:
            key = (str(e["table"]), int(e["address"]))
        except (KeyError, TypeError, ValueError):
            raise ValueError("each edit needs table and address") from None
        rows.setdefault(key, {"table": key[0], "address": key[1]}).update(e)
    return [r for r in sorted(rows.values(), key=lambda r: (TABLES.index(r["table"]) if r["table"] in TABLES
                                                            else 9, r["address"])) if r.get("keep", True)]


def _samples(result: Dict[str, Any]) -> Dict[Tuple[str, int], List[int]]:
    return {(r["table"], r["address"]): r["samples"] for r in result.get("readable", [])}


def to_points(result: Dict[str, Any], edits: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Point dicts (template / device format) from suggestions + edits; initial ``value`` from the last sample.
    Raises ``ValueError`` on invalid points or overlapping addresses."""
    samples = _samples(result)
    points: List[Dict[str, Any]] = []
    for r in _merge_edits(result, edits):
        table, addr = r["table"], r["address"]
        dt = r.get("datatype") or ("bool" if table in BIT_TABLES else "uint16")
        p: Dict[str, Any] = {"name": r.get("name") or f"{NAME_PREFIX.get(table, table)}_{addr}",
                             "table": table, "address": addr, "datatype": dt}
        if r.get("description"):
            p["description"] = r["description"]
        if dt == "string":
            p["count"] = int(r.get("count") or 1)
        for k in ("byte_order", "word_order"):
            if r.get(k) and r[k] != "big" and dt not in ("bool",):
                p[k] = r[k]
        for k, default in (("scale", 1.0), ("offset", 0.0)):
            if r.get(k) is not None and float(r[k]) != default:
                p[k] = float(r[k])
        if r.get("units"):
            p["units"] = r["units"]
        if table in ("coil", "holding"):
            p["access"] = "r"  # recorded from a real device: read-only by default
        try:
            pc = PointConfig.model_validate(p)
        except ValueError as e:
            raise ValueError(f"{table} {addr}: {e}") from None
        n = register_count(pc)
        words = [samples.get((table, addr + i), [None])[-1] for i in range(n)]
        if all(w is not None for w in words) and dt != "bit":
            try:
                p["value"] = _decode_value(dt, words, pc.byte_order or "big", pc.word_order or "big", n,
                                           pc.scale, pc.offset)
            except (ValueError, struct.error):
                pass
        points.append(p)
    names = [p["name"] for p in points]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"duplicate point names {dup}")
    check_layout("scan", [PointConfig.model_validate(p) for p in points])
    return points


def _ident_text(ident: Optional[Dict[str, str]]) -> str:
    if not ident:
        return ""
    return " ".join(v for k, v in ident.items() if k in ("vendor_name", "product_code", "model_name",
                                                           "product_name", "revision"))


def to_template(result: Dict[str, Any], edits: Optional[List[Dict[str, Any]]] = None, *, name: str = "scanned",
                title: Optional[str] = None, description: Optional[str] = None) -> Dict[str, Any]:
    """User-template dict ``{name, title, description, params, points}`` (sme_sim_common user template format).

    The scanned device identification is kept as the template's ``identification`` (an extra key the
    registry merges into devices built from it) and summarised in the description."""
    ident = _ident_text(result.get("identification"))
    desc = description or (f"Scanned from {result.get('target')} unit {result.get('unit')}"
                           + (f" ({ident})" if ident else ""))
    out = {"name": name, "title": title or (ident or name), "description": desc, "params": {},
           "points": to_points(result, edits)}
    if result.get("identification"):
        out["identification"] = dict(result["identification"])
    return out


def to_device(result: Dict[str, Any], edits: Optional[List[Dict[str, Any]]] = None, *, name: str,
              unit_id: int, listeners: Optional[List[str]] = None) -> Dict[str, Any]:
    """Device dict with inline points and the scanned identification."""
    dev: Dict[str, Any] = {"name": name, "unit_id": unit_id,
                           "description": f"Scanned from {result.get('target')} unit {result.get('unit')}",
                           "points": to_points(result, edits)}
    if listeners is not None:
        dev["listeners"] = listeners
    if result.get("identification"):
        dev["identification"] = dict(result["identification"])
    return dev


def template_yaml(tpl: Dict[str, Any], result: Optional[Dict[str, Any]] = None) -> str:
    """YAML text of ``tpl``; each point on one line followed by its guess (confidence, reason) as a comment."""
    guesses = {(s["table"], s["address"]): s for s in (result or {}).get("suggestions", [])}
    head = {k: v for k, v in tpl.items() if k != "points"}
    lines = ["# Generated by sme-modbus-sim scan (" + READ_ONLY_NOTE + ").",
             "# Datatypes are GUESSES - check each point against the device documentation.",
             yaml.safe_dump(head, allow_unicode=True, sort_keys=False).rstrip(), "points:"]
    for p in tpl["points"]:
        flow = yaml.safe_dump(p, default_flow_style=True, allow_unicode=True, sort_keys=False, width=10000).strip()
        g = guesses.get((p["table"], p["address"]))
        lines.append(f"  - {flow}")
        if g:
            lines.append(f"    # guess {g['datatype']} confidence {g['confidence']:.2f}: {g['reason']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- record

@dataclass
class _Read:
    table: str
    start: int
    count: int
    points: List[PointConfig]


def plan_reads(points: List[PointConfig], max_regs: int = 64, max_bits: int = 256) -> List[_Read]:
    """Group points into reads of exactly adjacent / overlapping address spans (never across a gap)."""
    out: List[_Read] = []
    for table in TABLES:
        ps = sorted((p for p in points if p.table == table), key=lambda p: p.address)
        cap = max_bits if table in BIT_TABLES else max_regs
        for p in ps:
            n = register_count(p)
            cur = out[-1] if out and out[-1].table == table else None
            if cur and p.address <= cur.start + cur.count and max(cur.start + cur.count, p.address + n) - cur.start <= cap:
                cur.count = max(cur.count, p.address + n - cur.start)
                cur.points.append(p)
            else:
                out.append(_Read(table, p.address, n, [p]))
    return out


def point_value(p: PointConfig, words: List[int], byte_order: str = "big", word_order: str = "big") -> Any:
    """Engineering value of point ``p`` from its raw words / bits (scale_ref is not applied)."""
    if p.datatype == "bool":
        return 1 if words[0] else 0
    if p.datatype == "bit":
        return 1 if get_bit(words[0], p.bit or 0) else 0
    return _decode_value(p.datatype or "uint16", words, p.byte_order or byte_order, p.word_order or word_order,
                         register_count(p), p.scale, p.offset)


def record_points(points: List[PointConfig]) -> List[PointConfig]:
    out = [p for p in points if p.table is not None and p.address is not None]
    if not out:
        raise ValueError("no points with table and address to record")
    return out


def record_plan(target: str, unit: int, points: List[PointConfig], period: float, duration: float,
                rate: float) -> Dict[str, Any]:
    if period <= 0 or duration < 0:
        raise ValueError("period must be > 0 and duration >= 0")
    reads = len(plan_reads(points))
    cycles = int(duration // period) + 1
    if reads / rate > period:
        raise ValueError(f"{reads} reads per cycle need {reads / rate:.1f} s at {rate:g} req/s, "
                         f"longer than the period {period:g} s")
    return {"kind": "record", "target": target, "unit": unit, "points": len(points), "requests_per_cycle": reads,
            "cycles": cycles, "requests": reads * cycles, "period_s": period, "duration_s": duration,
            "rate": rate, "read_only": READ_ONLY_NOTE}


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.10g}"
    return str(v)


async def record(client: ModbusClient, unit: int, points: List[PointConfig], period: float, duration: float,
                 out_csv: Path, *, byte_order: str = "big", word_order: str = "big", progress: Progress = None,
                 cancel: Optional[asyncio.Event] = None) -> Dict[str, Any]:
    """Poll ``points`` every ``period`` s for ``duration`` s into a replay CSV (header ``timestamp`` + point
    names; ISO 8601 local time with offset; failed reads -> empty cell). Rows are flushed as they are written,
    so a cancelled record keeps what it has (``Cancelled`` is not raised; ``cancelled`` is set)."""
    unit = check_unit(unit)
    points = record_points(points)
    reads = plan_reads(points)
    cycles = int(duration // period) + 1
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    failed, rows, cancelled = 0, 0, False
    missing_first: List[str] = []
    t0 = time.monotonic()
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", *[p.name for p in points]])
        f.flush()
        for i in range(cycles):
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            stamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
            values: Dict[str, Any] = {}
            for rd in reads:
                try:
                    data = await client.read(rd.table, unit, rd.start, rd.count)
                    for p in rd.points:
                        off = p.address - rd.start
                        values[p.name] = point_value(p, data[off:off + register_count(p)], byte_order, word_order)
                except (ModbusError, ClientError, ValueError):
                    for p in rd.points:  # block failed: fall back to one read per point
                        try:
                            data = await client.read(p.table, unit, p.address, register_count(p))
                            values[p.name] = point_value(p, data, byte_order, word_order)
                        except (ModbusError, ClientError, ValueError):
                            failed += 1
            if i == 0:
                missing_first = [p.name for p in points if p.name not in values]
            w.writerow([stamp, *[_cell(values.get(p.name)) for p in points]])
            f.flush()
            rows += 1
            if progress:
                progress({"phase": "record", "rows": rows, "cycles": cycles, "requests": client.requests,
                          "failed": failed})
            if i + 1 < cycles:
                delay = t0 + (i + 1) * period - time.monotonic()
                if delay > 0:
                    if cancel is not None:
                        try:
                            await asyncio.wait_for(cancel.wait(), delay)
                        except asyncio.TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(delay)
    warnings = []
    if missing_first:
        warnings.append(f"first row has empty cells for {missing_first}: replay rejects a column whose first "
                        "value is empty - fill those cells or delete the row before using the CSV for replay")
    return {"path": str(out_csv), "rows": rows, "cycles": cycles, "columns": [p.name for p in points],
            "failed_reads": failed, "requests": client.requests, "cancelled": cancelled, "warnings": warnings,
            "missing_first": missing_first}


# ---------------------------------------------------------------- background jobs (web)

class JobBusy(Exception):
    pass


class ScanJobs:
    """At most one scan / record job at a time. Progress is published as ``{"type": "scan", "job": {...}}``
    (the job summary without ``result``), throttled to 4 per second plus every state change."""

    def __init__(self, publish: Callable[[Dict[str, Any]], None],
                 log_event: Optional[Callable[..., Any]] = None) -> None:
        self.publish = publish
        self.log_event = log_event
        self.job: Optional[Dict[str, Any]] = None
        self._task: Optional[asyncio.Task] = None
        self._cancel: Optional[asyncio.Event] = None
        self._last_push = 0.0
        self._seq = 0

    @property
    def running(self) -> bool:
        return self.job is not None and self.job["state"] == "running"

    def summary(self, with_result: bool = False) -> Optional[Dict[str, Any]]:
        if self.job is None:
            return None
        if with_result:
            return dict(self.job)
        return {k: v for k, v in self.job.items() if k != "result"}

    def _push(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_push >= 0.25:
            self._last_push = now
            self.publish({"type": "scan", "job": self.summary()})

    def start(self, kind: str, plan: Dict[str, Any], client: ModbusClient,
              run: Callable[[Callable[[Dict[str, Any]], None], asyncio.Event], Any]) -> Dict[str, Any]:
        if self.running:
            raise JobBusy(f"a {self.job['kind']} job is already running")
        self._seq += 1
        self._cancel = asyncio.Event()
        self.job = {"id": self._seq, "kind": kind, "state": "running", "target": plan["target"],
                    "unit": plan["unit"], "plan": plan, "started": time.time(), "finished": None,
                    "progress": {}, "error": None, "result": None}
        job, cancel = self.job, self._cancel

        def progress(p: Dict[str, Any]) -> None:
            job["progress"] = p
            self._push()

        async def runner() -> None:
            try:
                async with client:
                    job["result"] = await run(progress, cancel)
                job["state"] = "cancelled" if cancel.is_set() else "done"
            except Cancelled:
                job["state"] = "cancelled"
            except (ModbusError, ClientError, OSError, ValueError) as e:
                job["state"], job["error"] = "error", str(e) or type(e).__name__
            except Exception as e:  # noqa: BLE001 - a job must never take the engine down
                job["state"], job["error"] = "error", f"{type(e).__name__}: {e}"
            job["finished"] = time.time()
            self._push(force=True)
            if self.log_event:
                level = "error" if job["state"] == "error" else "info"
                self.log_event(level, f"{kind} of {job['target']} unit {job['unit']}: {job['state']}"
                               + (f" ({job['error']})" if job["error"] else ""))

        self._task = asyncio.get_running_loop().create_task(runner())
        self._push(force=True)
        if self.log_event:
            self.log_event("info", f"{kind} of {plan['target']} unit {plan['unit']} started ({READ_ONLY_NOTE})")
        return self.summary()

    def cancel(self) -> bool:
        if not self.running or self._cancel is None:
            return False
        self._cancel.set()
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.wait({self._task})

    async def stop(self) -> None:
        self.cancel()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
