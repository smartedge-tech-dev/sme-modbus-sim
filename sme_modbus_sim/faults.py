"""Fault injection (design 5.4): runtime rules evaluated by the protocol servers.

Rules are runtime state (REST ``/api/faults``), not part of the YAML configuration; users keep
scenarios as separate preset files and load them with ``POST /api/faults/preset``.

Evaluation: the servers call ``FaultManager.evaluate`` once per request frame. Rules are checked
in order and the first rule that *fires* wins (a rule whose filters match but whose every_n /
probability gate says "not this time" lets evaluation continue with the next rule).
``freeze`` and ``refuse_connections`` are state toggles and never answer a request:
``freeze`` is queried by the engine tick (``is_frozen``), ``refuse_connections`` by the TCP
accept path (``refuse``).
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ACTIONS = ("exception", "delay", "no_response", "disconnect", "corrupt_crc", "truncate",
           "wrong_transaction_id", "wrong_unit_id", "busy", "freeze", "refuse_connections")
EXCEPTION_CODES = (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x08, 0x0A, 0x0B)
STATE_ACTIONS = ("freeze", "refuse_connections")
RTU_FRAMERS = ("rtu", "rtu_over_tcp", "ascii")  # framers with a checksum (CRC or LRC)
PARAM_KEYS = {"exception": {"code"}, "delay": {"ms", "min_ms", "max_ms"}, "truncate": {"bytes"},
              "busy": {"seconds"}, "wrong_unit_id": {"unit"}}
MAX_DELAY_MS = 600_000
PUBLISH_THROTTLE_S = 0.5
RUNTIME_KEYS = ("triggers", "matches", "active", "busy_remaining")


def _clean(data: Any) -> Any:
    """Drop the runtime fields of ``FaultManager.list()`` so a listed rule can be posted back."""
    return {k: v for k, v in data.items() if k not in RUNTIME_KEYS} if isinstance(data, dict) else data


def _code(v: Any) -> int:
    """Exception code: int, or a string read as hex ("04", "0A", "0x0B")."""
    if isinstance(v, bool):
        raise ValueError("exception code must be a number")
    n = int(v, 16) if isinstance(v, str) else int(v)
    if n not in EXCEPTION_CODES:
        raise ValueError(f"exception code {v!r} not one of 01-06, 08, 0A, 0B")
    return n


def _num(params: Dict[str, Any], key: str, lo: float, hi: float, kind=float):
    v = params[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"params.{key} must be a number in {lo}..{hi}")
    if kind is int and v != int(v):
        raise ValueError(f"params.{key} must be an integer")
    return kind(v)


class FaultSpec(BaseModel):
    """A fault rule as stored, exported and accepted by the API."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[str] = Field(None, min_length=1, max_length=64)
    name: Optional[str] = None
    enabled: bool = True
    listener: Optional[str] = None
    unit: Optional[int] = Field(None, ge=0, le=255)
    function_codes: Optional[List[int]] = None
    address_start: Optional[int] = Field(None, ge=0, le=65535)
    address_end: Optional[int] = Field(None, ge=0, le=65535)
    action: Literal[ACTIONS]  # type: ignore[valid-type]
    params: Dict[str, Any] = Field(default_factory=dict)
    probability: float = Field(1.0, ge=0.0, le=1.0)
    every_n: Optional[int] = Field(None, ge=1)
    remaining: Optional[int] = Field(None, ge=0)

    @field_validator("function_codes")
    @classmethod
    def _fcs(cls, v):
        if v is not None:
            if not v:
                return None
            for fc in v:
                if not 1 <= fc <= 127:
                    raise ValueError(f"function code {fc} out of 1..127")
        return v

    @model_validator(mode="after")
    def _check(self):
        if self.address_start is not None and self.address_end is not None and self.address_start > self.address_end:
            raise ValueError("address_start must be <= address_end")
        p = dict(self.params)
        unknown = set(p) - PARAM_KEYS.get(self.action, set())
        if unknown:
            raise ValueError(f"params {sorted(unknown)} not valid for action {self.action!r}")
        if self.action == "exception":
            p["code"] = _code(p.get("code", 4))
        elif self.action == "delay":
            if "ms" in p:
                if "min_ms" in p or "max_ms" in p:
                    raise ValueError("delay: give either params.ms or params.min_ms/max_ms")
                p["ms"] = _num(p, "ms", 0, MAX_DELAY_MS)
            elif "min_ms" in p and "max_ms" in p:
                p["min_ms"], p["max_ms"] = _num(p, "min_ms", 0, MAX_DELAY_MS), _num(p, "max_ms", 0, MAX_DELAY_MS)
                if p["min_ms"] > p["max_ms"]:
                    raise ValueError("delay: params.min_ms must be <= params.max_ms")
            else:
                raise ValueError("delay: params.ms or params.min_ms + params.max_ms required")
        elif self.action == "truncate":
            p["bytes"] = _num(p, "bytes", 0, 300, int) if "bytes" in p else 3
        elif self.action == "busy":
            p["seconds"] = _num(p, "seconds", 0.001, 86400) if "seconds" in p else 10.0
        elif self.action == "wrong_unit_id" and "unit" in p:
            p["unit"] = _num(p, "unit", 0, 255, int)
        self.params = p
        return self


@dataclass
class FaultHit:
    rule: str
    action: str
    code: Optional[int] = None      # exception / busy
    delay: float = 0.0              # seconds
    nbytes: Optional[int] = None    # truncate
    unit: Optional[int] = None      # wrong_unit_id override

    @property
    def info(self) -> Dict[str, Any]:
        return {"rule": self.rule, "action": self.action}


def request_ranges(pdu: bytes) -> List[tuple]:
    """Address ranges (inclusive) touched by a request PDU; empty for FCs without addresses."""
    if len(pdu) < 3:
        return []
    fc, start = pdu[0], int.from_bytes(pdu[1:3], "big")
    if fc in (1, 2, 3, 4, 15, 16) and len(pdu) >= 5:
        return [(start, start + max(int.from_bytes(pdu[3:5], "big"), 1) - 1)]
    if fc in (5, 6, 22):
        return [(start, start)]
    if fc == 23 and len(pdu) >= 9:
        ws = int.from_bytes(pdu[5:7], "big")
        return [(start, start + max(int.from_bytes(pdu[3:5], "big"), 1) - 1),
                (ws, ws + max(int.from_bytes(pdu[7:9], "big"), 1) - 1)]
    return []


class FaultRule:
    def __init__(self, spec: FaultSpec) -> None:
        self.spec = spec
        self.triggers = 0
        self.matches = 0
        self.busy_until: Optional[float] = None

    def to_dict(self, now: float) -> Dict[str, Any]:
        d = self.spec.model_dump()
        active = self.spec.enabled and (self.spec.action in STATE_ACTIONS
                                        or (self.busy_until is not None and now < self.busy_until))
        d.update(triggers=self.triggers, matches=self.matches, active=bool(active),
                 busy_remaining=round(self.busy_until - now, 3) if active and self.busy_until else None)
        return d


class FaultManager:
    def __init__(self, publish: Optional[Callable[[Dict[str, Any]], None]] = None, *,
                 rng: Optional[random.Random] = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.rules: List[FaultRule] = []
        self.publish = publish
        self.rng = rng or random.Random()
        self.clock = clock
        self._next = 1
        self._flush: Optional[asyncio.TimerHandle] = None

    # ------------------------------------------------------------ CRUD
    def list(self) -> List[Dict[str, Any]]:
        now = self.clock()
        return [r.to_dict(now) for r in self.rules]

    def export(self) -> List[Dict[str, Any]]:
        return [r.spec.model_dump(exclude_none=True, exclude_defaults=False) for r in self.rules]

    def _index(self, rule_id: str) -> int:
        for i, r in enumerate(self.rules):
            if r.spec.id == rule_id:
                return i
        raise KeyError(f"unknown fault rule {rule_id!r}")

    def _new_id(self, taken: Iterable[str]) -> str:
        taken = set(taken)
        while f"f{self._next}" in taken:
            self._next += 1
        self._next += 1
        return f"f{self._next - 1}"

    def add(self, data: Dict[str, Any]) -> Dict[str, Any]:
        spec = FaultSpec.model_validate(_clean(data))
        ids = [r.spec.id for r in self.rules]
        if spec.id is None:
            spec.id = self._new_id(ids)
        elif spec.id in ids:
            raise ValueError(f"fault rule {spec.id!r} already exists")
        rule = FaultRule(spec)
        self.rules.append(rule)
        self._changed()
        return rule.to_dict(self.clock())

    def update(self, rule_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Merge ``data`` into the rule (omitted fields keep their value; a new action drops old params)."""
        i = self._index(rule_id)
        old = self.rules[i]
        data = _clean(data)
        base = old.spec.model_dump()
        if data.get("action", base["action"]) != base["action"] and "params" not in data:
            base["params"] = {}
        spec = FaultSpec.model_validate({**base, **data, "id": rule_id})
        rule = FaultRule(spec)
        if old.spec.action == spec.action:
            rule.triggers, rule.matches = old.triggers, old.matches
        self.rules[i] = rule
        self._changed()
        return rule.to_dict(self.clock())

    def delete(self, rule_id: str) -> None:
        del self.rules[self._index(rule_id)]
        self._changed()

    def clear(self) -> None:
        self.rules = []
        self._changed()

    def load(self, items: Any, replace: bool = True) -> List[Dict[str, Any]]:
        """Load a preset (list of rules, or {"faults": [...]}); all-or-nothing."""
        if isinstance(items, dict) and "faults" in items:
            items = items["faults"]
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ValueError("a fault preset is a list of rules (or a mapping with a 'faults' list)")
        specs = []
        for n, item in enumerate(items, 1):
            try:
                specs.append(FaultSpec.model_validate(_clean(item)))
            except Exception as e:
                raise ValueError(f"rule #{n}: {e}") from None
        ids = [] if replace else [r.spec.id for r in self.rules]
        for s in specs:
            if s.id is not None and s.id in ids:
                raise ValueError(f"duplicate fault rule id {s.id!r}")
            ids.append(s.id)
        for s in specs:
            if s.id is None:
                s.id = self._new_id([x for x in ids if x])
                ids.append(s.id)
        new = [FaultRule(s) for s in specs]
        self.rules = new if replace else self.rules + new
        self._changed()
        return self.list()

    # ------------------------------------------------------------ publish
    def _emit(self) -> None:
        self._flush = None
        if self.publish is not None:
            self.publish({"type": "faults", "rules": self.list()})

    def _changed(self) -> None:
        if self._flush is not None:
            self._flush.cancel()
        self._emit()

    def _touched(self) -> None:
        """Trigger counters changed: publish at most every PUBLISH_THROTTLE_S."""
        if self.publish is None or self._flush is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._emit()
            return
        self._flush = loop.call_later(PUBLISH_THROTTLE_S, self._emit)

    # ------------------------------------------------------------ evaluation
    def _gate(self, r: FaultRule) -> bool:
        s = r.spec
        r.matches += 1
        if s.remaining is not None and s.remaining <= 0:
            return False
        if s.every_n and r.matches % s.every_n:
            return False
        if s.probability < 1.0 and self.rng.random() >= s.probability:
            return False
        if s.remaining is not None:
            s.remaining -= 1
            if s.remaining == 0 and s.action != "busy":
                s.enabled = False
        return True

    def _hit(self, r: FaultRule) -> FaultHit:
        r.triggers += 1
        s, p = r.spec, r.spec.params
        hit = FaultHit(s.id or "", s.action)
        if s.action == "exception":
            hit.code = p["code"]
        elif s.action == "busy":
            hit.code = 0x06
        elif s.action == "delay":
            ms = p["ms"] if "ms" in p else self.rng.uniform(p["min_ms"], p["max_ms"])
            hit.delay = ms / 1000.0
        elif s.action == "truncate":
            hit.nbytes = p["bytes"]
        elif s.action == "wrong_unit_id":
            hit.unit = p.get("unit")
        self._touched()
        return hit

    @staticmethod
    def _applies(action: str, framer: str) -> bool:
        if action == "corrupt_crc":
            return framer in RTU_FRAMERS
        if action == "wrong_transaction_id":
            return framer == "tcp"
        return True

    def evaluate(self, listener_id: str, framer: str, unit_id: int, pdu: bytes) -> Optional[FaultHit]:
        if not self.rules or not pdu:
            return None
        now, fc = self.clock(), pdu[0]
        ranges = None
        for r in self.rules:
            s = r.spec
            if not s.enabled or s.action in STATE_ACTIONS or not self._applies(s.action, framer):
                continue
            if s.listener is not None and s.listener != listener_id:
                continue
            if s.unit is not None and s.unit != unit_id:
                continue
            if s.function_codes and fc not in s.function_codes:
                continue
            if s.address_start is not None or s.address_end is not None:
                lo = s.address_start if s.address_start is not None else 0
                hi = s.address_end if s.address_end is not None else 65535
                ranges = request_ranges(pdu) if ranges is None else ranges
                if not any(a <= hi and b >= lo for a, b in ranges):
                    continue
            if s.action == "busy" and r.busy_until is not None:
                if now < r.busy_until:
                    return self._hit(r)
                r.busy_until = None          # window over: the rule has done its job
                s.enabled = False
                self._changed()
                continue
            if not self._gate(r):
                continue
            if s.action == "busy":
                r.busy_until = now + s.params["seconds"]
            return self._hit(r)
        return None

    def refuse(self, listener_id: str) -> Optional[FaultHit]:
        for r in self.rules:
            s = r.spec
            if s.enabled and s.action == "refuse_connections" and s.listener in (None, listener_id) and self._gate(r):
                return self._hit(r)
        return None

    def is_frozen(self, unit_id: int, listeners: Iterable[str]) -> bool:
        for r in self.rules:
            s = r.spec
            if (s.enabled and s.action == "freeze" and s.unit in (None, unit_id)
                    and (s.listener is None or s.listener in listeners)):
                return True
        return False
