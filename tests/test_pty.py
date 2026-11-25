"""``port: pty`` serial listener: config rules everywhere, a real pty pair on POSIX.

The end-to-end test opens the ``link`` symlink with pyserial as the Modbus master, the way a
device under test (e.g. a modbus_tk program) would.
"""
import asyncio
import os

import pytest

from sme_modbus_sim import config as config_mod
from sme_modbus_sim.config import SimConfig
from sme_modbus_sim.protocol.framer import RtuFramer

posix_only = pytest.mark.skipif(os.name != "posix", reason="pseudo-terminals need POSIX")


def _cfg(*serial, **point):
    return SimConfig.model_validate({
        "listeners": [{"id": "t", "port": 5020}, *serial],
        "devices": [{"name": "D", "unit_id": 1, "points": [
            {"name": "sp", "table": "holding", "address": 0, "value": 5, "min": 0, "max": 100, **point}]}],
    })


@pytest.fixture
def posix(monkeypatch):
    monkeypatch.setattr(config_mod, "pty_supported", lambda: True)


# ----------------------------------------------------------------- config rules
def test_pty_port_normalised_and_link_optional(posix):
    cfg = _cfg({"id": "a", "type": "rtu", "port": "PTY"}, {"id": "b", "type": "rtu", "port": "pty", "link": "/tmp/v1"})
    a, b = cfg.listeners[1], cfg.listeners[2]
    assert (a.port, a.link) == ("pty", None) and (b.port, b.link) == ("pty", "/tmp/v1")


def test_pty_rejected_on_windows(monkeypatch):
    monkeypatch.setattr(config_mod, "pty_supported", lambda: False)
    with pytest.raises(ValueError, match="Linux, macOS or WSL2.*rtu_over_tcp"):
        _cfg({"id": "s", "type": "rtu", "port": "pty"})


@pytest.mark.parametrize("listener, msg", [
    ({"id": "s", "type": "rtu", "port": "COM5", "link": "/tmp/v0"}, "link only applies to port: pty"),
    ({"id": "s", "type": "tcp", "port": 5021, "link": "/tmp/v0"}, "only apply to type rtu"),
])
def test_link_needs_pty(posix, listener, msg):
    with pytest.raises(ValueError, match=msg):
        _cfg(listener)


def test_duplicate_link_rejected(posix):
    with pytest.raises(ValueError, match="share the same link"):
        _cfg({"id": "a", "type": "rtu", "port": "pty", "link": "/tmp/v0"},
             {"id": "b", "type": "rtu", "port": "pty", "link": "/tmp/v0"})


# ------------------------------------------------------------------ real ptys
@posix_only
def test_pty_port_link_lifecycle(tmp_path):
    from sme_modbus_sim.protocol.pty_port import PtyPort

    link = str(tmp_path / "ttyV0")
    p = PtyPort(link)
    try:
        assert os.readlink(link) == p.device and p.device.startswith("/dev/")
        with pytest.raises(OSError, match="already exists"):
            PtyPort(link)
        assert os.readlink(link) == p.device  # the existing link is left alone
    finally:
        p.close()
    assert not os.path.lexists(link)


@posix_only
def test_pty_link_not_ours_is_kept(tmp_path):
    from sme_modbus_sim.protocol.pty_port import PtyPort

    link = tmp_path / "ttyV0"
    link.symlink_to("/dev/null")
    with pytest.raises(OSError, match="already exists"):
        PtyPort(str(link))
    assert os.readlink(link) == "/dev/null"


@posix_only
def test_engine_pty_listener_end_to_end(tmp_path):
    serial = pytest.importorskip("serial")
    from sme_modbus_sim.engine import Engine

    link = str(tmp_path / "ttyV0")

    def transact(master, req: bytes, n: int) -> bytes:
        master.reset_input_buffer()
        master.write(req)
        return master.read(n)

    async def run():
        e = Engine(_cfg({"id": "s", "type": "rtu", "port": "pty", "link": link}),
                   listener_overrides={"t": {"port": 0}})
        await e.start()
        try:
            lst = {x["id"]: x for x in e.snapshot()["listeners"]}
            assert lst["s"]["status"] == "running", lst["s"]
            assert lst["s"]["port"] == link and lst["s"]["device"] == os.readlink(link)
            loop = asyncio.get_running_loop()
            master = await loop.run_in_executor(None, lambda: serial.Serial(link, 19200, timeout=2))
            try:
                async def tx(pdu: str, n: int) -> bytes:
                    return await loop.run_in_executor(
                        None, transact, master, RtuFramer.build(1, bytes.fromhex(pdu)), n)

                assert await tx("0300000001", 7) == RtuFramer.build(1, bytes([3, 2, 0, 5]))
                assert await tx("060000002A", 8) == RtuFramer.build(1, bytes.fromhex("060000002A"))
                assert await tx("0300000001", 7) == RtuFramer.build(1, bytes([3, 2, 0, 42]))
            finally:
                master.close()
            # the device under test may close and reopen its end
            master = await loop.run_in_executor(None, lambda: serial.Serial(link, 19200, timeout=2))
            try:
                resp = await loop.run_in_executor(
                    None, transact, master, RtuFramer.build(1, bytes.fromhex("0300000001")), 7)
                assert resp == RtuFramer.build(1, bytes([3, 2, 0, 42]))
            finally:
                master.close()
        finally:
            await e.stop()
        assert not os.path.lexists(link)

    asyncio.run(run())
