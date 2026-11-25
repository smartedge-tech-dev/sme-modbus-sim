"""Template ``identification`` defaults (sme-sim-common extra_keys) merge into devices."""
from sme_modbus_sim import templates as tpl
from sme_modbus_sim.config import parse_text
from sme_modbus_sim.engine import identification_objects


def _user_template(tmp_path, monkeypatch):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "scanned_meter.yaml").write_text(
        "name: scanned_meter\ntitle: Scanned meter\nparams: {}\n"
        "identification: {vendor_name: ACME, product_code: M-1, revision: '2.0'}\n"
        "points:\n  - {name: v, table: input, address: 0, datatype: uint16}\n", encoding="utf-8")
    tpl.load_user_templates(d)
    monkeypatch.setattr(tpl.registry, "_user_dir", d)


def test_template_identification_is_merged_under_device_fields(tmp_path, monkeypatch):
    _user_template(tmp_path, monkeypatch)
    try:
        cfg = parse_text(
            "devices:\n"
            "  - {name: M1, unit_id: 1, template: scanned_meter}\n"
            "  - {name: M2, unit_id: 2, template: scanned_meter, identification: {product_code: M-2}}\n")
        m1, m2 = cfg.devices
        assert identification_objects(m1)[0] == "ACME" and identification_objects(m1)[1] == "M-1"
        o2 = identification_objects(m2)
        assert o2[0] == "ACME" and o2[1] == "M-2" and o2[2] == "2.0"
    finally:
        tpl.clear_user_templates()
