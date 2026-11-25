"""Datatype encoding between engineering values and 16-bit register words.

Byte/word order:
  * ``byte_order`` -- order of the two bytes inside each 16-bit register
    (``big`` = high byte first, the Modbus default).
  * ``word_order`` -- order of registers for multi-register values
    (``big`` = most significant word at the lowest address).
  big/big = ABCD, big/little = CDAB, little/big = BADC, little/little = DCBA.

Engineering conversion: ``raw = (value - offset) / scale``. Integer types round to
the nearest integer and raise ``ValueError`` when the raw value does not fit.
Float types store the unrounded quotient.

``string`` values occupy ``count`` registers (2 bytes each), ASCII, right padded
with NUL. ``word_order`` is NOT applied to strings (characters stay in address
order); ``byte_order`` swaps the two characters inside each register.
"""
from __future__ import annotations

import math
import struct

BYTE_ORDERS = ("big", "little")
WORD_ORDERS = ("big", "little")

# datatype -> (struct format char, register count, is_float)
_NUMERIC = {
    "uint16": ("H", 1, False),
    "int16": ("h", 1, False),
    "uint32": ("I", 2, False),
    "int32": ("i", 2, False),
    "float32": ("f", 2, True),
    "uint64": ("Q", 4, False),
    "int64": ("q", 4, False),
    "float64": ("d", 4, True),
}
_INT_RANGE = {
    "uint16": (0, 0xFFFF),
    "int16": (-0x8000, 0x7FFF),
    "uint32": (0, 0xFFFFFFFF),
    "int32": (-0x80000000, 0x7FFFFFFF),
    "uint64": (0, 0xFFFFFFFFFFFFFFFF),
    "int64": (-0x8000000000000000, 0x7FFFFFFFFFFFFFFF),
}

DATATYPES = ("bool", "bit", "string", *_NUMERIC.keys())


def register_count(datatype: str, count: int = 1) -> int:
    """Number of protocol addresses (registers or bits) a point occupies.

    ``bool`` occupies one bit address; ``bit`` occupies (part of) one register;
    ``string`` occupies ``count`` registers.
    """
    if datatype in ("bool", "bit"):
        return 1
    if datatype == "string":
        if count < 1:
            raise ValueError("string count must be >= 1")
        return count
    try:
        return _NUMERIC[datatype][1]
    except KeyError:
        raise ValueError(f"unknown datatype: {datatype!r}") from None


def _check_orders(byte_order: str, word_order: str) -> None:
    if byte_order not in BYTE_ORDERS:
        raise ValueError(f"invalid byte_order: {byte_order!r}")
    if word_order not in WORD_ORDERS:
        raise ValueError(f"invalid word_order: {word_order!r}")


def _bytes_to_words(data: bytes, byte_order: str, word_order: str) -> list[int]:
    words = [(data[i] << 8) | data[i + 1] for i in range(0, len(data), 2)]
    if word_order == "little":
        words.reverse()
    if byte_order == "little":
        words = [((w & 0xFF) << 8) | (w >> 8) for w in words]
    return words


def _words_to_bytes(words: list[int], byte_order: str, word_order: str) -> bytes:
    words = [int(w) & 0xFFFF for w in words]
    if byte_order == "little":
        words = [((w & 0xFF) << 8) | (w >> 8) for w in words]
    if word_order == "little":
        words = list(reversed(words))
    return b"".join(struct.pack(">H", w) for w in words)


def encode(
    value,
    datatype: str,
    byte_order: str = "big",
    word_order: str = "big",
    scale: float = 1.0,
    offset: float = 0.0,
    count: int = 1,
) -> list[int]:
    """Encode an engineering value into a list of register words (or ``[0|1]`` for bool)."""
    _check_orders(byte_order, word_order)
    if datatype in ("bool", "bit"):
        return [1 if value else 0]
    if datatype == "string":
        n = register_count("string", count)
        raw = str(value).encode("ascii")
        if len(raw) > 2 * n:
            raise ValueError(f"string too long for {n} registers: {value!r}")
        raw = raw.ljust(2 * n, b"\x00")
        # word_order intentionally ignored for strings (see module docstring)
        return _bytes_to_words(raw, byte_order, "big")
    try:
        fmt, _, is_float = _NUMERIC[datatype]
    except KeyError:
        raise ValueError(f"unknown datatype: {datatype!r}") from None
    if scale == 0:
        raise ValueError("scale must not be 0")
    raw = (float(value) - offset) / scale if (scale != 1 or offset != 0) else value
    if is_float:
        try:
            data = struct.pack(">" + fmt, float(raw))
        except (OverflowError, struct.error) as exc:
            raise ValueError(f"value out of range for {datatype}: {value!r}") from exc
    else:
        if isinstance(raw, float):
            if not math.isfinite(raw):
                raise ValueError(f"non-finite value for {datatype}: {value!r}")
            raw = round(raw)
        raw = int(raw)
        lo, hi = _INT_RANGE[datatype]
        if not lo <= raw <= hi:
            raise ValueError(f"raw value {raw} out of range for {datatype} [{lo}, {hi}]")
        data = struct.pack(">" + fmt, raw)
    return _bytes_to_words(data, byte_order, word_order)


def decode(
    words,
    datatype: str,
    byte_order: str = "big",
    word_order: str = "big",
    scale: float = 1.0,
    offset: float = 0.0,
    count: int = 1,
):
    """Decode register words into an engineering value.

    Integer types return ``int`` when scale == 1 and offset == 0, otherwise ``float``.
    """
    _check_orders(byte_order, word_order)
    words = list(words)
    if datatype in ("bool", "bit"):
        return bool(words[0])
    n = register_count(datatype, count)
    if len(words) != n:
        raise ValueError(f"{datatype} needs {n} registers, got {len(words)}")
    if datatype == "string":
        data = _words_to_bytes(words, byte_order, "big")
        return data.rstrip(b"\x00").decode("ascii", errors="replace")
    fmt, _, is_float = _NUMERIC[datatype]
    raw = struct.unpack(">" + fmt, _words_to_bytes(words, byte_order, word_order))[0]
    if scale == 1 and offset == 0:
        return raw
    return raw * scale + offset


def get_bit(register: int, bit: int) -> bool:
    """Return bit ``bit`` (0 = LSB) of a 16-bit register."""
    if not 0 <= bit <= 15:
        raise ValueError(f"bit index out of range: {bit}")
    return bool((int(register) >> bit) & 1)


def set_bit(register: int, bit: int, on: bool) -> int:
    """Return ``register`` with bit ``bit`` set or cleared."""
    if not 0 <= bit <= 15:
        raise ValueError(f"bit index out of range: {bit}")
    register = int(register) & 0xFFFF
    return (register | (1 << bit)) if on else (register & ~(1 << bit) & 0xFFFF)
