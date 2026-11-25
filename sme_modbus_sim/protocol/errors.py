"""Protocol error types."""
from __future__ import annotations

from .constants import ExceptionCode


class ModbusError(Exception):
    """Raised by store / PDU handling; converted into a Modbus exception response."""

    def __init__(self, exception_code: int, message: str = "") -> None:
        self.exception_code = int(exception_code)
        try:
            name = ExceptionCode(self.exception_code).name
        except ValueError:
            name = f"0x{self.exception_code:02X}"
        super().__init__(message or name)
        self.message = message or name
