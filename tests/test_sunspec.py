"""SunSpec templates: generator layout, scale-factor / not-implemented mapping, and pysunspec2 as an
independent client (Apache-2.0; skipped when not installed)."""

import asyncio
import json

import pytest

from sme_modbus_sim import __version__, sunspec
from sme_modbus_sim.config import DeviceConfig, SimConfig
from sme_modbus_sim.engine import Engine
from sme_modbus_sim.protocol import datatypes as dt
from sme_modbus_sim.templates import TEMPLATES, expand_device, template_catalog


def _points(template, **params):
    return {p.name: p for p in expand_device(DeviceConfig(name="d", unit_id=1, template=template, params=params))}


def _engine(template, running=True, **params):
    cfg = SimConfig.model_validate({
        "simulation": {"tick_seconds": 0.1, "time_scale": 10, "running": running},
        "listeners": [{"id": "tcp1", "host": "127.0.0.1", "port": 15020}],
        "devices": [{"name": "dev", "unit_id": 1, "template": template, "params": params}]})
    return Engine(cfg, listener_overrides={"tcp1": {"host": "127.0.0.1", "port": 0}})


def test_vendored_models_and_readme():
    assert set(sunspec.available_models()) >= {1, 103, 120, 121, 122, 123, 160, 203, 701, 702, 703, 704, 705, 802, 803}
    readme = (sunspec.DATA_DIR / "README.md").read_text(encoding="utf-8")
    assert sunspec.SOURCE_COMMIT in readme and "modified: no" in readme.lower()
    assert "Apache License" in (sunspec.DATA_DIR / "LICENSE").read_text(encoding="utf-8")
    assert json.loads((sunspec.DATA_DIR / "model_103.json").read_text(encoding="utf-8"))["id"] == 103


@pytest.mark.parametrize("base", [40000, 0, 50000])
def test_chain_layout(base):
    pts = _points("sunspec_pv_inverter", base_address=base, mppt_count=3)
    assert pts["SunS"].address == base and pts["SunS"].value == 0x53756E53
    addr = base + 2
    for mid in (1, 103, 120, 121, 122, 123, 160):
        assert pts[f"m{mid}_ID"].address == addr and pts[f"m{mid}_ID"].value == mid
        length = pts[f"m{mid}_L"].value
        assert length == sunspec.model_length(mid, {"module": 3} if mid == 160 else None)
        addr += 2 + length
    assert pts["m1_L"].value == 66 and pts["m103_L"].value == 50 and pts["m160_L"].value == 8 + 20 * 3
    assert pts["end_ID"].address == addr and pts["end_ID"].value == 0xFFFF and pts["end_L"].value == 0
    assert pts["m160_N"].value == 3 and "m160_module3_DCW" in pts and "m160_module4_DCW" not in pts
    assert pts["m103_W"].scale_ref == "m103_W_SF" and pts["m103_W_SF"].datatype == "sunspec_sf"
    assert pts["m160_module1_DCA"].scale_ref == "m160_DCA_SF"
    assert pts["m123_WMaxLimPct"].access == "rw" and pts["m103_W"].access == "r"
    assert pts["m103_St"].labels[4] == "MPPT" and pts["m1_Vr"].value == __version__


def test_bad_base_address_rejected():
    with pytest.raises(ValueError):
        _points("sunspec_meter", base_address=1000)


def test_catalog_wording():
    entries = {e["name"]: e for e in template_catalog()}
    for name in ("sunspec_pv_inverter", "sunspec_meter", "sunspec_der", "sunspec_battery"):
        text = (entries[name]["title"] + " " + entries[name]["description"]).lower()
        assert "not affiliated with, endorsed by, or certified by sunspec alliance" in text
        for bad in ("compatible", "compliant", "certified sunspec"):
            assert bad not in text.replace("or certified by", "")
    assert TEMPLATES["sunspec_meter"]["params"]["base_address"] == 40000


def test_scale_factor_and_not_implemented_mapping():
    eng = _engine("sunspec_pv_inverter", rated_w=10000)
    dev = eng.devices["dev"]
    a, a_sf = dev.by_name["m103_A"], dev.by_name["m103_A_SF"]
    assert a_sf.read() == -2 and a_sf.raw() == [dt.encode(-2, "int16")[0]]
    a.set_value(12.34)
    assert a.raw() == [1234] and a.read() == pytest.approx(12.34)
    hz = dev.by_name["m103_Hz"]
    assert hz.raw() == [5000]
    # optional point without a value: SunSpec "not implemented" sentinel <-> None
    vnd = dev.by_name["m103_StVnd"]
    assert vnd.raw() == [0xFFFF] and vnd.json_value() is None
    tmp = dev.by_name["m103_TmpSnk"]
    assert tmp.raw() == [0x8000] and tmp.read() is None
    assert dev.by_name["m122_Tms"].raw() == [0xFFFF, 0xFFFF]
    assert dev.by_name["m122_VArAval_SF"].raw() == [0x8000]
    # accumulators are never null
    assert dev.by_name["m103_WH"].read() == 0


def test_controls_cap_output():
    eng = _engine("sunspec_pv_inverter", rated_w=10000)
    dev = eng.devices["dev"]
    names = {"m123_WMaxLim_Ena": 1, "m123_WMaxLimPct": 30}
    for k, v in names.items():
        eng.set_point("dev", k, v)
    w = dev.by_name["m103_W"]
    w.set_value(9000)
    exprs = w.behavior
    env = {p.ident: p.value for p in dev.points}
    env.update({"hour": 12.0, "t": 43200.0, "dt": 1.0, "pv": None})
    assert exprs.step(env, 9000, 1.0) <= 3000 + 1e-6
    env["m123_Conn"] = 0
    assert exprs.step(env, 9000, 1.0) == 0


def test_sunspec_sf_requires_valid_reference():
    with pytest.raises(ValueError, match="scale_ref"):
        SimConfig.model_validate({"devices": [{"name": "d", "unit_id": 1, "points": [
            {"name": "w", "table": "holding", "address": 0, "datatype": "int16", "scale_ref": "nope"}]}]})
    with pytest.raises(ValueError, match="scale_ref"):
        SimConfig.model_validate({"devices": [{"name": "d", "unit_id": 1, "points": [
            {"name": "w", "table": "holding", "address": 0, "datatype": "float32", "scale_ref": "sf"},
            {"name": "sf", "table": "holding", "address": 2, "datatype": "sunspec_sf", "value": 0}]}]})


def test_pysunspec2_scan_reads_models_with_scale():
    client_mod = pytest.importorskip("sunspec2.modbus.client")

    async def main():
        eng = _engine("sunspec_pv_inverter", running=False, rated_w=10000, mppt_count=2, serial_number="SN-42")
        await eng.start()
        try:
            dev = eng.devices["dev"]
            dev.by_name["m103_W"].set_value(4321)
            dev.by_name["m103_WH"].set_value(123456)
            dev.by_name["m103_A"].set_value(6.26)
            port = eng.servers["tcp1"].bound_port

            def scan():
                d = client_mod.SunSpecModbusClientDeviceTCP(slave_id=1, ipaddr="127.0.0.1", ipport=port,
                                                            timeout=3)
                try:
                    d.scan()
                    m103 = d.models[103][0]
                    return (sorted(k for k in d.models if isinstance(k, int)), d.base_addr,
                            d.models[1][0].Mn.cvalue, d.models[1][0].SN.cvalue,
                            m103.W.cvalue, m103.WH.cvalue, m103.A.cvalue, m103.Hz.cvalue,
                            m103.StVnd.cvalue, d.models[160][0].N.cvalue, len(d.models[160][0].module))
                finally:
                    d.close()

            return await asyncio.to_thread(scan)
        finally:
            await eng.stop()

    ids, base, mn, sn, w, wh, a, hz, vnd, n, modules = asyncio.run(main())
    assert ids == [1, 103, 120, 121, 122, 123, 160]
    assert base == 40000 and mn == "SmartEdge" and sn == "SN-42"
    assert w == pytest.approx(4321) and wh == 123456
    assert a == pytest.approx(6.26) and hz == pytest.approx(50, abs=0.1)
    assert vnd is None and n == 2 and modules == 2


def test_pysunspec2_scan_meter_at_base_50000():
    client_mod = pytest.importorskip("sunspec2.modbus.client")

    async def main():
        eng = _engine("sunspec_meter", running=False, base_address=50000)
        await eng.start()
        try:
            eng.devices["dev"].by_name["m203_W"].set_value(-1234)
            eng.devices["dev"].by_name["m203_TotWhImp"].set_value(987)
            port = eng.servers["tcp1"].bound_port

            def scan():
                d = client_mod.SunSpecModbusClientDeviceTCP(slave_id=1, ipaddr="127.0.0.1", ipport=port,
                                                            timeout=3)
                try:
                    d.scan()
                    m = d.models[203][0]
                    return (sorted(k for k in d.models if isinstance(k, int)), d.base_addr,
                            m.W.cvalue, m.TotWhImp.cvalue, d.models[1][0].Md.cvalue)
                finally:
                    d.close()

            return await asyncio.to_thread(scan)
        finally:
            await eng.stop()

    ids, base, w, imp, md = asyncio.run(main())
    assert ids == [1, 203] and base == 50000 and md == "SME-SIM-METER"
    assert w == pytest.approx(-1234) and imp == 987


# ---------------------------------------------------------------- DER (701-705) / battery (802, 803)

def _scan(eng, fn):
    client_mod = pytest.importorskip("sunspec2.modbus.client")

    async def main():
        await eng.start()
        try:
            port = eng.servers["tcp1"].bound_port

            def scan():
                d = client_mod.SunSpecModbusClientDeviceTCP(slave_id=1, ipaddr="127.0.0.1", ipport=port,
                                                            timeout=3)
                try:
                    d.scan()
                    return fn(d)
                finally:
                    d.close()

            return await asyncio.to_thread(scan)
        finally:
            await eng.stop()

    return asyncio.run(main())


def test_der_and_battery_chain_layout():
    pts = _points("sunspec_der", vv_curves=2, vv_points=3)
    addr = 40002
    for mid in (1, 701, 702, 703, 704, 705):
        assert pts[f"m{mid}_ID"].address == addr and pts[f"m{mid}_ID"].value == mid
        assert pts[f"m{mid}_L"].value == sunspec.model_length(mid, {"Crv": 2, "Pt": 3} if mid == 705 else None)
        addr += 2 + pts[f"m{mid}_L"].value
    assert pts["end_ID"].address == addr and pts["end_ID"].value == 0xFFFF
    # nested repeating groups: NCrv / NPt (both top-level points) take the group counts
    assert pts["m705_NCrv"].value == 2 and pts["m705_NPt"].value == 3 and pts["m705_L"].value == 13 + 2 * (10 + 2 * 3)
    assert "m705_Crv2_Pt3_V" in pts and "m705_Crv3_Pt1_V" not in pts and "m705_Crv1_Pt4_V" not in pts
    assert pts["m705_Crv1_Pt1_V"].scale_ref == "m705_V_SF" and pts["m705_Crv1_Pt1_Var"].scale_ref == "m705_DeptRef_SF"
    assert pts["m704_PFWInj_PF"].scale_ref == "m704_PF_SF" and pts["m704_WSet"].access == "rw"
    assert pts["m701_W"].scale_ref == "m701_W_SF" and pts["m701_InvSt"].labels[4] == "THROTTLED"

    pts = _points("sunspec_battery", string_count=3)
    addr = 40002
    for mid in (1, 802, 803):
        assert pts[f"m{mid}_ID"].address == addr
        addr += 2 + pts[f"m{mid}_L"].value
    assert pts["end_ID"].address == addr
    assert pts["m802_L"].value == 62 and pts["m803_NStr"].value == 3 and pts["m803_L"].value == 26 + 3 * 32
    assert "m803_string3_StrSoC" in pts and "m803_string4_StrSoC" not in pts
    assert pts["m803_string1_Pad1"].value == 0x8000 and pts["m802_SoC"].scale_ref == "m802_SoC_SF"


def test_der_controls_drive_measured_power():
    eng = _engine("sunspec_der", rated_w=10000)
    dev = eng.devices["dev"]
    beh = dev.by_name["m701_W"].behavior

    def step(cur, **kw):
        env = {p.ident: p.value for p in dev.points}
        env.update({"hour": 12.0, "t": 43200.0, "dt": 1.0, "pv": None, **kw})
        return beh.step(env, cur, 1.0)

    assert step(0) == pytest.approx(10000)  # PV-like availability at noon, WRmp 100 %/s
    assert step(0, m704_WSetEna=1, m704_WSet=-4000) == pytest.approx(-4000)  # absorb on setpoint
    assert step(0, m704_WSetEna=1, m704_WSetMod=0, m704_WSetPct=25) == pytest.approx(2500)
    assert step(0, m704_WMaxLimPctEna=1, m704_WMaxLimPct=30) == pytest.approx(3000)
    assert step(0, m704_WRmp=10) == pytest.approx(1000)  # ramp-up limited to 10 % of WMax per second
    assert step(5000, m703_ES=0) == 0  # enter service disabled -> no output


def test_battery_soc_follows_power():
    eng = _engine("sunspec_battery", capacity_wh=10000, max_w=5000, initial_soc=50)
    dev = eng.devices["dev"]
    env = {p.ident: p.value for p in dev.points}
    env.update({"hour": 12.0, "t": 43200.0, "dt": 1.0, "pv": None})
    w = dev.by_name["m802_W"].behavior.step(env, 0, 1.0)
    assert w == pytest.approx(-3000)  # noon: charging (W < 0)
    env["m802_W"] = w
    soc = dev.by_name["m802_SoC"].behavior.step(env, 50, 3600.0)
    assert soc == pytest.approx(80)  # 3 kW for 1 h into 10 kWh = +30 %
    env.update({"m802_SoC": 95})
    assert dev.by_name["m802_W"].behavior.step(env, 0, 1.0) == 0  # full: stop charging
    env.update({"m802_SoC": 50, "m802_SetOp": 2})
    assert dev.by_name["m802_W"].behavior.step(env, 0, 1.0) == 0  # DISCONNECT


def test_pysunspec2_scan_der():
    eng = _engine("sunspec_der", running=False, rated_w=10000, vv_curves=2, vv_points=4, serial_number="DER-7")
    dev = eng.devices["dev"]
    dev.by_name["m701_W"].set_value(-4321)
    dev.by_name["m701_TotWhInj"].set_value(123456)
    dev.by_name["m704_WSet"].set_value(-5000)

    def read(d):
        m701, m704, m705 = d.models[701][0], d.models[704][0], d.models[705][0]
        return (sorted(k for k in d.models if isinstance(k, int)), d.models[1][0].SN.cvalue,
                m701.W.cvalue, m701.TotWhInj.cvalue, m701.Hz.cvalue, m701.LLV.cvalue, m701.TmpSnk.cvalue,
                d.models[702][0].WMaxRtg.cvalue, d.models[703][0].ESHzHi.cvalue, m704.WSet.cvalue,
                m704.PFWInj.PF.cvalue, m705.NCrv.cvalue, len(m705.Crv), len(m705.Crv[1].Pt),
                m705.Crv[0].Pt[0].V.cvalue, m705.Crv[0].Pt[3].Var.cvalue)

    (ids, sn, w, wh, hz, llv, snk, wrtg, eshz, wset, pf, ncrv, crvs, npts, v1, q4) = _scan(eng, read)
    assert ids == [1, 701, 702, 703, 704, 705] and sn == "DER-7"
    assert w == pytest.approx(-4321) and wh == 123456 and hz == pytest.approx(50, abs=0.1)
    assert llv == pytest.approx(230 * 3 ** 0.5, abs=0.1) and snk is None
    assert wrtg == pytest.approx(10000) and eshz == pytest.approx(50.1) and wset == -5000
    assert pf == pytest.approx(1) and ncrv == 2 and crvs == 2 and npts == 4
    assert v1 == pytest.approx(92) and q4 == pytest.approx(-44)


def test_pysunspec2_scan_battery_at_base_0():
    eng = _engine("sunspec_battery", running=False, base_address=0, capacity_wh=20000, string_count=2,
                  initial_soc=42.5)
    eng.devices["dev"].by_name["m802_W"].set_value(-1500)

    def read(d):
        b, li = d.models[802][0], d.models[803][0]
        return (sorted(k for k in d.models if isinstance(k, int)), d.base_addr, b.SoC.cvalue, b.WHRtg.cvalue,
                b.W.cvalue, b.Typ.cvalue, b.CellV_SF.cvalue, li.NStr.cvalue, len(li.string),
                li.string[1].StrNMod.cvalue)

    ids, base, soc, whrtg, w, typ, cvsf, nstr, strings, nmod = _scan(eng, read)
    assert ids == [1, 802, 803] and base == 0
    assert soc == pytest.approx(42.5) and whrtg == pytest.approx(20000) and w == pytest.approx(-1500)
    assert typ == 4 and cvsf == -3 and nstr == 2 and strings == 2 and nmod == 8
