"""Point maps generated from the public SunSpec model definitions (Apache-2.0).

The model JSON files in ``data/sunspec/`` are vendored unmodified from https://github.com/sunspec/models
(see ``data/sunspec/README.md`` for the pinned commit). This module turns a chain of model ids into
simulator point dicts: the ``SunS`` marker at the base address, each model block (``ID``, ``L`` and its
points, repeating groups expanded) and the end model (``0xFFFF``, ``L = 0``).

Not affiliated with, endorsed by, or certified by SunSpec Alliance.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DATA_DIR = Path(__file__).parent / "data" / "sunspec"
SOURCE_REPO = "https://github.com/sunspec/models"
SOURCE_COMMIT = "90b4a331dcca1d6eac69c1bead952fddcc5852e0"
BASE_ADDRESSES = (40000, 0, 50000)
SUNS_MARKER = 0x53756E53  # "SunS"
END_MODEL_ID = 0xFFFF

Pt = Dict[str, Any]

# SunSpec type -> (simulator datatype, nullable). Accumulators / strings / pad have no "not implemented" value
# other than 0 / empty, so they are not nullable.
TYPE_MAP: Dict[str, Tuple[str, bool]] = {
    "int16": ("int16", True), "uint16": ("uint16", True), "count": ("uint16", True),
    "acc16": ("uint16", False), "enum16": ("enum", True), "bitfield16": ("uint16", True),
    "int32": ("int32", True), "uint32": ("uint32", True), "acc32": ("uint32", False),
    "enum32": ("uint32", True), "bitfield32": ("uint32", True),
    "int64": ("int64", True), "uint64": ("uint64", True), "acc64": ("uint64", False),
    "float32": ("float32", True), "float64": ("float64", True),
    "sunssf": ("sunspec_sf", True), "pad": ("uint16", False),
    "ipaddr": ("uint32", False), "eui48": ("uint64", False),
    "string": ("string", False), "ipv6addr": ("string", False),
}
PAD_VALUE = 0x8000


@lru_cache(maxsize=None)
def load_model(model_id: int) -> Dict[str, Any]:
    path = DATA_DIR / f"model_{int(model_id)}.json"
    if not path.is_file():
        known = ", ".join(str(m) for m in available_models())
        raise ValueError(f"SunSpec model {model_id} is not bundled (available: {known})")
    return json.loads(path.read_text(encoding="utf-8"))


def available_models() -> List[int]:
    return sorted(int(p.stem.split("_")[1]) for p in DATA_DIR.glob("model_*.json"))


def model_point(model_id: int, name: str) -> Dict[str, Any]:
    """Top-level point definition of a model (raises KeyError when absent)."""
    for p in load_model(model_id)["group"]["points"]:
        if p["name"] == name:
            return p
    raise KeyError(f"model {model_id} has no point {name!r}")


def _size(p: Dict[str, Any]) -> int:
    return int(p.get("size", 1))


def _group_size(g: Dict[str, Any], counts: Dict[str, int]) -> int:
    n = sum(_size(p) for p in g["points"])
    for sub in g.get("groups", []):
        n += _group_size(sub, counts) * _count(sub, counts)
    return n


def _count(g: Dict[str, Any], counts: Dict[str, int]) -> int:
    c = g.get("count", 1)
    if isinstance(c, str):  # count held by a point (e.g. model 160 "N"): from params, default 1
        return int(counts.get(g["name"], 1))
    return int(counts.get(g["name"], c))


def model_length(model_id: int, counts: Optional[Dict[str, int]] = None) -> int:
    """Model length ``L`` (registers after ``ID`` and ``L``)."""
    return _group_size(load_model(model_id)["group"], counts or {}) - 2


def build_points(models: Sequence[int], base_address: int = 40000,
                 values: Optional[Dict[str, Any]] = None,
                 overrides: Optional[Dict[str, Dict[str, Any]]] = None,
                 counts: Optional[Dict[int, Dict[str, int]]] = None,
                 prefix: str = "") -> List[Pt]:
    """Holding-register point dicts for a SunSpec model chain.

    Point names are ``m<id>_<Name>``; points of a repeating group are ``m<id>_<group><i>_<Name>`` (1-based),
    of a single nested group ``m<id>_<group>_<Name>``. ``values`` sets initial values by point name and
    ``overrides`` merges extra fields (behavior, min, max, ...). A point without a value is 0 / "" when the
    model marks it mandatory, otherwise ``None`` = SunSpec "not implemented". ``counts`` gives repeating-group
    counts per model id (``{160: {"module": 2}}``); the point holding the count gets that value.
    """
    values, overrides, counts = dict(values or {}), dict(overrides or {}), dict(counts or {})
    if base_address not in BASE_ADDRESSES:
        raise ValueError(f"SunSpec base address must be one of {BASE_ADDRESSES}, got {base_address}")
    out: List[Pt] = [{"name": f"{prefix}SunS", "table": "holding", "address": base_address, "datatype": "uint32",
                      "access": "r", "value": SUNS_MARKER, "description": "SunSpec marker 'SunS' (0x53756e53)"}]
    addr = base_address + 2

    def emit(mid: int, g: Dict[str, Any], scope: List[Dict[str, str]], label: str, top: bool,
             mcounts: Dict[str, int]) -> None:
        nonlocal addr
        local: Dict[str, str] = {}
        scope = [local, *scope]
        # count points referenced by sub-groups (e.g. "N") take the group count
        count_points = {sub["count"]: _count(sub, mcounts) for sub in g.get("groups", [])
                        if isinstance(sub.get("count"), str)}
        for p in g["points"]:
            local[p["name"]] = f"{prefix}m{mid}_{label}{p['name']}"
        for p in g["points"]:
            name = local[p["name"]]
            stype = p["type"]
            dt, nullable = TYPE_MAP.get(stype, ("uint16", True))
            size = _size(p)
            pt: Pt = {"name": name, "table": "holding", "address": addr, "datatype": dt,
                      "access": "rw" if p.get("access", "R") == "RW" else "r"}
            if dt == "string":
                pt["count"] = size
            elif size != {"int64": 4, "uint64": 4, "int32": 2, "uint32": 2, "float32": 2,
                          "float64": 4}.get(dt, 1):
                pt.update({"datatype": "string", "count": size})
                dt, nullable = "string", False
            if nullable:
                pt["nullable"] = True
            desc = " - ".join(x for x in (p.get("label"), p.get("desc")) if x)
            if desc:
                pt["description"] = desc
            if p.get("units"):
                pt["units"] = p["units"]
            sf = p.get("sf")
            if isinstance(sf, str):
                ref = next((s[sf] for s in scope if sf in s), None)
                if ref is None:
                    raise ValueError(f"model {mid}: point {p['name']} sf {sf!r} not found")
                pt["scale_ref"] = ref
            elif isinstance(sf, int):
                pt["scale"] = 10.0 ** sf
            if dt == "enum" and p.get("symbols"):
                pt["labels"] = {int(s["value"]): s["name"] for s in p["symbols"]}
            elif p.get("symbols"):
                bits = ", ".join(f"{s['value']}={s['name']}" for s in p["symbols"])
                pt["description"] = f"{pt.get('description', '')} [{bits}]".strip()
            # value
            if top and p["name"] == "ID":
                v: Any = mid
            elif top and p["name"] == "L":
                v = model_length(mid, mcounts)
            elif name in values:
                v = values[name]
            elif p["name"] in count_points:
                v = count_points[p["name"]]
            elif stype == "pad":
                v = PAD_VALUE
            elif dt == "string":
                v = ""
            elif p.get("mandatory") == "M" or not nullable:
                v = 0
            else:
                v = None
            pt["value"] = v
            pt.update(overrides.get(name, {}))
            if pt["value"] is None and pt.get("behavior"):
                pt["value"] = "" if dt == "string" else 0  # simulated: implemented
            out.append(pt)
            addr += size
        for sub in g.get("groups", []):
            n = _count(sub, mcounts)
            for i in range(1, n + 1):
                sub_label = f"{label}{sub['name']}{i if sub.get('count', 1) != 1 else ''}_"
                emit(mid, sub, scope, sub_label, False, mcounts)

    for mid in models:
        emit(int(mid), load_model(int(mid))["group"], [], "", True, counts.get(int(mid), {}))
    out.append({"name": f"{prefix}end_ID", "table": "holding", "address": addr, "datatype": "uint16",
                "access": "r", "value": END_MODEL_ID, "description": "SunSpec end model ID (0xFFFF)"})
    out.append({"name": f"{prefix}end_L", "table": "holding", "address": addr + 1, "datatype": "uint16",
                "access": "r", "value": 0, "description": "SunSpec end model length"})
    if addr + 1 > 65535:
        raise ValueError("SunSpec model chain exceeds holding address 65535")
    return out


def sf_for(max_value: float, register_max: int = 32767, min_sf: int = 0) -> int:
    """Smallest scale factor >= ``min_sf`` such that ``max_value`` fits the register."""
    sf = min_sf
    while abs(max_value) / (10.0 ** sf) > register_max and sf < 10:
        sf += 1
    return sf
