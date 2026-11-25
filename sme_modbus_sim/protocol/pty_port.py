"""Pseudo-terminal pair for the serial RTU listener (``port: pty``), POSIX only.

ModbusSim keeps the master end and reads/writes it like a serial port; the device under test
opens the slave end (``/dev/pts/N``, or the ``link`` symlink that points to it) as its serial
port. No driver and no root are needed.

The slave end stays open in this process too, so a device under test that closes and reopens
the port never makes reads on the master fail with EIO (Linux) or EOF (macOS).

Limits: a pty carries no modem control lines (RTS/CTS/DTR) and has no baud rate, so the
parity/baudrate settings are ignored and byte timing is not real (t1.5/t3.5 cannot be tested).
"""
from __future__ import annotations

import os
import select

PTY_PORT = "pty"
READ_CHUNK = 4096


def pty_supported() -> bool:
    return os.name == "posix"


class PtyPort:
    """Minimal pyserial-like wrapper around the master end of a pty pair."""

    def __init__(self, link: str | None = None, timeout: float = 0.01) -> None:
        if not pty_supported():
            raise OSError("port: pty needs Linux, macOS or WSL2 (POSIX pseudo-terminals)")
        import tty

        self.timeout = timeout
        self.link: str | None = None
        self._master, self._slave = os.openpty()
        try:
            tty.setraw(self._slave)
            os.set_blocking(self._master, False)
            self.device = os.ttyname(self._slave)
            if link:
                try:
                    os.symlink(self.device, link)
                except FileExistsError:
                    raise OSError(f"link {link!r} already exists; remove it or choose another path") from None
                self.link = link
        except BaseException:
            os.close(self._master)
            os.close(self._slave)
            raise

    @property
    def in_waiting(self) -> int:
        return 0  # read() returns whatever has arrived, so the caller never needs a count

    def read(self, size: int = 1) -> bytes:
        if self._master < 0:
            raise OSError("pty closed")
        ready, _, _ = select.select([self._master], [], [], self.timeout)
        if not ready:
            return b""
        try:
            return os.read(self._master, max(size, READ_CHUNK))
        except BlockingIOError:
            return b""

    def write(self, data: bytes) -> int:
        view = memoryview(data)
        while view:
            select.select([], [self._master], [], 1.0)
            try:
                n = os.write(self._master, view)
            except BlockingIOError:
                continue
            view = view[n:]
        return len(data)

    def cancel_read(self) -> None:
        """No-op: read() returns within ``timeout``."""

    def close(self) -> None:
        if self.link:
            try:
                if os.readlink(self.link) == self.device:
                    os.unlink(self.link)
            except OSError:
                pass
            self.link = None
        for fd in (self._master, self._slave):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._master = self._slave = -1
