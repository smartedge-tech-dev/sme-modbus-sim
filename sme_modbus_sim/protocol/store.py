"""Per-unit register image with defined address ranges and access control.

Two access paths:
  * ``get`` / ``set``     -- raw access for the engine; bypasses definition and access checks.
  * ``read`` / ``write``  -- protocol access; raises ``ModbusError`` like a real device.

Simulator choices (not mandated by the Modbus spec):
  * ``undefined_address="zero"`` only relaxes READS (undefined addresses read as 0).
    Writes touching an undefined address always raise exception 02, so a master can
    never silently "succeed" writing into a hole.
  * Writing a defined but read-only (``writable=False``) address raises exception 02.
    The spec does not say which code a read-only register should produce; 02 is what
    most field devices return, so we follow that.
"""
from __future__ import annotations

from typing import Callable, Iterable

from .constants import (
    ADDRESS_SPACE,
    BIT_TABLES,
    TABLES,
    WRITABLE_TABLES,
    ExceptionCode,
)
from .errors import ModbusError

UNDEFINED_POLICIES = ("exception", "zero")

WriteCallback = Callable[[str, int, int], None]
# Validator receives (table, address, values) before the write is applied and may
# raise ModbusError to reject it (e.g. min/max checks, partial multi-register writes).
WriteValidator = Callable[[str, int, list], None]


class UnitStore:
    def __init__(self, unit_id: int, undefined_address: str = "exception") -> None:
        if undefined_address not in UNDEFINED_POLICIES:
            raise ValueError(f"invalid undefined_address policy: {undefined_address!r}")
        self.unit_id = unit_id
        self.undefined_address = undefined_address
        self._values: dict[str, dict[int, int]] = {t: {} for t in TABLES}
        self._defined: dict[str, set[int]] = {t: set() for t in TABLES}
        self._writable: dict[str, set[int]] = {t: set() for t in TABLES}
        self._on_write: list[WriteCallback] = []
        self._validators: list[WriteValidator] = []
        # FC2B/0E Read Device Identification objects: object id -> value (str or bytes).
        self.identification: dict[int, str | bytes] = {}

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _check_table(table: str) -> None:
        if table not in TABLES:
            raise ValueError(f"unknown table: {table!r}")

    def define(self, table: str, address: int, count: int = 1, writable: bool | None = None) -> None:
        """Mark ``count`` addresses starting at ``address`` as existing.

        ``writable`` defaults to True for coil/holding and must be False for discrete/input.
        """
        self._check_table(table)
        if count < 1 or address < 0 or address + count > ADDRESS_SPACE:
            raise ValueError(f"invalid range {table}[{address}:{address + count}]")
        if writable is None:
            writable = table in WRITABLE_TABLES
        if writable and table not in WRITABLE_TABLES:
            raise ValueError(f"table {table!r} cannot be writable")
        rng = range(address, address + count)
        self._defined[table].update(rng)
        if writable:
            self._writable[table].update(rng)
        else:
            self._writable[table].difference_update(rng)

    def is_defined(self, table: str, address: int) -> bool:
        return address in self._defined[table]

    def is_writable(self, table: str, address: int) -> bool:
        return address in self._writable[table]

    def defined_addresses(self, table: str) -> list[int]:
        return sorted(self._defined[table])

    def on_write(self, callback: WriteCallback) -> None:
        """Register ``callback(table, address, count)`` invoked after a successful protocol write."""
        self._on_write.append(callback)

    def add_write_validator(self, validator: WriteValidator) -> None:
        """Register ``validator(table, address, values)`` run before a protocol write is applied."""
        self._validators.append(validator)

    # ------------------------------------------------------------- raw access
    def get(self, table: str, address: int, count: int = 1) -> list[int]:
        self._check_table(table)
        vals = self._values[table]
        return [vals.get(a, 0) for a in range(address, address + count)]

    def set(self, table: str, address: int, values: Iterable[int]) -> None:
        self._check_table(table)
        vals = self._values[table]
        is_bit = table in BIT_TABLES
        for i, v in enumerate(values):
            a = address + i
            if not 0 <= a < ADDRESS_SPACE:
                raise ValueError(f"address out of range: {a}")
            vals[a] = (1 if v else 0) if is_bit else (int(v) & 0xFFFF)

    # -------------------------------------------------------- protocol access
    @staticmethod
    def _check_span(address: int, count: int) -> None:
        if address < 0 or address + count > ADDRESS_SPACE:
            raise ModbusError(ExceptionCode.ILLEGAL_DATA_ADDRESS, "address range exceeds 0xFFFF")

    def read(self, table: str, address: int, count: int) -> list[int]:
        self._check_table(table)
        self._check_span(address, count)
        if self.undefined_address == "exception":
            defined = self._defined[table]
            for a in range(address, address + count):
                if a not in defined:
                    raise ModbusError(ExceptionCode.ILLEGAL_DATA_ADDRESS, f"{table}[{a}] undefined")
        return self.get(table, address, count)

    def write(self, table: str, address: int, values: list[int]) -> None:
        self._check_table(table)
        count = len(values)
        self._check_span(address, count)
        if table not in WRITABLE_TABLES:
            raise ModbusError(ExceptionCode.ILLEGAL_DATA_ADDRESS, f"{table} is read-only")
        defined = self._defined[table]
        writable = self._writable[table]
        for a in range(address, address + count):
            if a not in defined:
                raise ModbusError(ExceptionCode.ILLEGAL_DATA_ADDRESS, f"{table}[{a}] undefined")
            if a not in writable:
                raise ModbusError(ExceptionCode.ILLEGAL_DATA_ADDRESS, f"{table}[{a}] read-only")
        for validator in self._validators:
            validator(table, address, list(values))
        self.set(table, address, values)
        for cb in self._on_write:
            cb(table, address, count)
