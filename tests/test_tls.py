"""Modbus/TCP Security listener (type: tls): handshake, client certificates, roles, authorization."""

import asyncio
import datetime as dt
import ssl

import pytest

x509 = pytest.importorskip("cryptography.x509")
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from sme_modbus_sim.config import parse_text  # noqa: E402
from sme_modbus_sim.engine import Engine  # noqa: E402
from sme_modbus_sim.protocol.framer import MbapFramer  # noqa: E402
from sme_modbus_sim.protocol.server import ModbusTcpServer  # noqa: E402
from sme_modbus_sim.protocol.tls import (ROLE_OID, Authorizer, cert_role,  # noqa: E402
                                         client_context, server_context)
from tests.test_protocol_server import make_units  # noqa: E402

READ0 = bytes([3, 0, 0, 0, 2])
WRITE5 = bytes([6, 0, 5, 0x04, 0xD2])  # FC 06 holding 5 = 1234
NOW = dt.datetime.now(dt.timezone.utc)


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _role_ext(role: str) -> x509.UnrecognizedExtension:
    utf8 = role.encode()
    return x509.UnrecognizedExtension(x509.ObjectIdentifier(ROLE_OID), bytes([0x0C, len(utf8)]) + utf8)


def _issue(cn, issuer=None, *, ca=False, roles=(), critical=False, serial=None):
    key = ec.generate_private_key(ec.SECP256R1())
    issuer_name, issuer_key = (issuer[0].subject, issuer[1]) if issuer else (_name(cn), key)
    b = (x509.CertificateBuilder().subject_name(_name(cn)).issuer_name(issuer_name)
         .public_key(key.public_key()).serial_number(serial or x509.random_serial_number())
         .not_valid_before(NOW - dt.timedelta(days=1)).not_valid_after(NOW + dt.timedelta(days=30))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if not ca:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
    for r in roles:
        b = b.add_extension(_role_ext(r), critical=critical)
    return b.sign(issuer_key, hashes.SHA256()), key


def _write(dirpath, name, cert, key):
    c = dirpath / f"{name}.crt"
    k = dirpath / f"{name}.key"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    return str(c), str(k)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("pki")
    ca = _issue("Test CA", ca=True)
    other = _issue("Other CA", ca=True)
    p = {"dir": d, "ca": _write(d, "ca", *ca)[0], "other_ca": _write(d, "other_ca", *other)[0]}
    p["server"] = _write(d, "server", *_issue("server", ca))
    p["operator"] = _write(d, "operator", *_issue("op", ca, roles=["operator"]))
    p["viewer"] = _write(d, "viewer", *_issue("view", ca, roles=["viewer"]))
    p["norole"] = _write(d, "norole", *_issue("nobody", ca))
    p["critical"] = _write(d, "critical", *_issue("crit", ca, roles=["operator"], critical=True))
    p["stranger"] = _write(d, "stranger", *_issue("stranger", other, roles=["operator"]))
    revoked_cert, revoked_key = _issue("revoked", ca, roles=["operator"], serial=4242)
    p["revoked"] = _write(d, "revoked", revoked_cert, revoked_key)
    crl = (x509.CertificateRevocationListBuilder().issuer_name(ca[0].subject)
           .last_update(NOW - dt.timedelta(days=1)).next_update(NOW + dt.timedelta(days=30))
           .add_revoked_certificate(x509.RevokedCertificateBuilder().serial_number(4242)
                                    .revocation_date(NOW - dt.timedelta(hours=1)).build())
           .sign(ca[1], hashes.SHA256()))
    p["crl"] = str(d / "ca.crl")
    (d / "ca.crl").write_bytes(crl.public_bytes(serialization.Encoding.PEM))
    return p


def _der(path):
    return ssl.PEM_cert_to_DER_cert(open(path).read())


def test_cert_role_parsing(pki):
    assert cert_role(_der(pki["operator"][0])) == "operator"
    assert cert_role(_der(pki["critical"][0])) == "operator"
    assert cert_role(_der(pki["norole"][0])) is None
    assert cert_role(b"\x30\x03\x02\x01") is None  # truncated
    assert cert_role(None) is None


def test_authorizer():
    a = Authorizer({"op": "all", "view": "read", "writer": [3, 6, 16]})
    assert a.allowed("op", 0x10) and a.allowed("op", 0x2B)
    assert a.allowed("view", 3) and a.allowed("view", 0x2B) and not a.allowed("view", 6)
    assert a.allowed("writer", 6) and not a.allowed("writer", 4) and not a.allowed("writer", 5)
    assert a.allowed("nobody", 4) and not a.allowed("nobody", 5)
    assert a.allowed(None, 1) and not a.allowed(None, 15)


def run_tls(pki, body, *, roles=None, client_auth=True, crl=False, ca=True):
    async def main():
        units = make_units()
        events = []
        ctx = server_context(*pki["server"], pki["ca"] if ca else None, client_auth=client_auth,
                             crl=pki["crl"] if crl else None)
        srv = ModbusTcpServer("127.0.0.1", 0, resolver=units.get, trace=events.append, default_unit=1,
                              listener_id="S1", ssl_context=ctx,
                              authorizer=Authorizer(roles) if roles is not None else None)
        await srv.start()
        try:
            await body(srv, events, units)
        finally:
            await srv.stop()
    asyncio.run(main())


async def tls_session(port, who, pki, requests, *, ca="ca"):
    """Open one TLS connection as ``who`` (a key of ``pki`` or None), send each request, return answers."""
    cert, key = pki[who] if who else (None, None)
    ctx = client_context(pki[ca], cert, key)
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port, ssl=ctx), 3)
    out = []
    try:
        for tid, pdu in enumerate(requests, 1):
            writer.write(MbapFramer.build(tid, 1, pdu))
            await writer.drain()
            head = await asyncio.wait_for(reader.readexactly(7), 3)
            body = await reader.readexactly(int.from_bytes(head[4:6], "big") - 1)
            out.append(body)
    finally:
        writer.close()
    return out


async def handshake_fails(port, who, pki, **kw):
    try:
        await tls_session(port, who, pki, [READ0], **kw)
    except (ssl.SSLError, ConnectionError, asyncio.IncompleteReadError, OSError):
        return True
    return False


def test_mutual_tls_read_write(pki):
    async def body(srv, events, units):
        assert srv.kind == "tls"
        r = await tls_session(srv.bound_port, "norole", pki, [READ0, WRITE5])
        assert r == [bytes([3, 4, 0, 100, 0, 101]), WRITE5]  # no roles map: no authorization
        assert units[1].get("holding", 5, 1) == [1234]
    run_tls(pki, body)


def test_client_certificate_required(pki):
    async def body(srv, events, units):
        assert await handshake_fails(srv.bound_port, None, pki)
        assert await handshake_fails(srv.bound_port, "stranger", pki)  # issued by another CA
        await asyncio.sleep(0.05)
        fails = [e for e in events if e.summary == "TLS handshake failed"]
        assert len(fails) == 2 and all(e.error.startswith("TLS handshake failed") for e in fails)
        # the listener keeps serving
        assert await tls_session(srv.bound_port, "operator", pki, [READ0])
    run_tls(pki, body)


def test_client_rejects_untrusted_server(pki):
    async def body(srv, events, units):
        assert await handshake_fails(srv.bound_port, "operator", pki, ca="other_ca")
    run_tls(pki, body)


def test_client_auth_optional(pki):
    async def body(srv, events, units):
        assert await tls_session(srv.bound_port, None, pki, [READ0])
    run_tls(pki, body, client_auth=False)
    run_tls(pki, body, ca=False, client_auth=False)


def test_role_authorization(pki):
    roles = {"operator": "all", "viewer": "read", "tuner": [3, 6]}

    async def body(srv, events, units):
        port = srv.bound_port
        assert (await tls_session(port, "operator", pki, [WRITE5]))[0] == WRITE5
        for who in ("viewer", "norole"):  # read-only role, no role
            read, write = await tls_session(port, who, pki, [READ0, WRITE5])
            assert read[0] == 3 and write == bytes([0x86, 0x01])
        denied = [e for e in events if e.error and e.error.startswith("not authorized")]
        assert [e.error for e in denied] == ["not authorized (role 'viewer')", "not authorized (no role)"]
        tx = [e for e in events if e.direction == "tx" and e.exception_code == 1]
        assert len(tx) == 2
        # connection stays open after a refusal
        assert (await tls_session(port, "viewer", pki, [WRITE5, READ0]))[1][0] == 3
    run_tls(pki, body, roles=roles)


def test_role_fc_list(pki):
    async def body(srv, events, units):
        r = await tls_session(srv.bound_port, "operator", pki, [READ0, WRITE5, bytes([4, 0, 0, 0, 1])])
        assert r[0][0] == 3 and r[1] == WRITE5 and r[2] == bytes([0x84, 0x01])
    run_tls(pki, body, roles={"operator": [3, 6]})


def test_critical_role_extension_rejected_by_openssl(pki):
    # OpenSSL refuses certificates with an unknown critical extension: documented limitation
    async def body(srv, events, units):
        assert await handshake_fails(srv.bound_port, "critical", pki)
        await asyncio.sleep(0.05)
        assert any("TLS handshake failed" in (e.error or "") for e in events), [e.error for e in events]
    run_tls(pki, body, roles={"operator": "all"})


def test_crl_revokes_client(pki):
    async def body(srv, events, units):
        assert await handshake_fails(srv.bound_port, "revoked", pki)
        assert await tls_session(srv.bound_port, "operator", pki, [READ0])
    run_tls(pki, body, crl=True)


def test_tls_hardening(pki):
    ctx = server_context(*pki["server"], pki["ca"])
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2 and ctx.options & ssl.OP_NO_COMPRESSION
    names = {c["name"] for c in ctx.get_ciphers()}
    assert "ECDHE-RSA-AES128-GCM-SHA256" in names  # R-14 mandatory suite
    assert not any(n.endswith("-SHA") or "MD5" in n or "CBC" in n for n in names)

    async def body(srv, events, units):
        cctx = client_context(pki["ca"], *pki["operator"])
        with pytest.warns(DeprecationWarning):
            cctx.maximum_version = ssl.TLSVersion.TLSv1_1
        with pytest.raises((ssl.SSLError, ValueError, ConnectionError)):
            await asyncio.open_connection("127.0.0.1", srv.bound_port, ssl=cctx)
    run_tls(pki, body)


def test_pymodbus_tls_client(pki):
    from pymodbus.client import AsyncModbusTlsClient

    async def body(srv, events, units):
        sslctx = client_context(pki["ca"], *pki["operator"])
        client = AsyncModbusTlsClient("127.0.0.1", port=srv.bound_port, sslctx=sslctx, timeout=2, retries=0)
        try:
            await client.connect()
            rr = await client.read_holding_registers(0, count=2, device_id=1)
            assert rr.registers == [100, 101]
            assert not (await client.write_register(5, 7, device_id=1)).isError()
        finally:
            client.close()
        assert units[1].get("holding", 5, 1) == [7]
    run_tls(pki, body, roles={"operator": "all"})


CONFIG = """
listeners:
  - {id: s1, type: tls, host: 127.0.0.1, certificate: server.crt, private_key: server.key, ca: ca.crt,
     roles: {operator: all, viewer: read}}
devices:
  - name: D
    unit_id: 1
    points:
      - {name: sp, table: holding, address: 0, datatype: int16, value: 5}
"""


def test_config_tls_fields():
    cfg = parse_text(CONFIG)
    lc = cfg.listeners[0]
    assert lc.port == 8020 and lc.client_auth is True and lc.roles == {"operator": "all", "viewer": "read"}
    with pytest.raises(ValueError, match="needs ca"):
        parse_text(CONFIG.replace(" ca: ca.crt,", ""))
    with pytest.raises(ValueError, match="crl and roles need ca"):
        parse_text(CONFIG.replace(" ca: ca.crt,", " client_auth: false,"))
    with pytest.raises(ValueError, match="needs certificate"):
        parse_text(CONFIG.replace("certificate: server.crt, ", ""))
    with pytest.raises(ValueError, match="only apply to type tls"):
        parse_text(CONFIG.replace("type: tls", "type: tcp"))
    with pytest.raises(ValueError, match="1..127"):
        parse_text(CONFIG.replace("viewer: read", "viewer: [3, 200]"))


def test_engine_tls_relative_paths(pki):
    async def main():
        d = pki["dir"]
        eng = Engine(parse_text(CONFIG), base_dir=d, listener_overrides={"s1": {"port": 0}})
        await eng.start()
        try:
            port = eng.servers["s1"].bound_port
            assert (await tls_session(port, "viewer", pki, [bytes([3, 0, 0, 0, 1])]))[0] == bytes([3, 2, 0, 5])
            assert eng.stats_snapshot()["s1"]["type"] == "tls"
        finally:
            await eng.stop()
        with pytest.raises(ValueError, match="cannot load TLS"):
            Engine(parse_text(CONFIG.replace("ca.crt", "missing.crt")), base_dir=d,
                   listener_overrides={"s1": {"port": 0}})._make_servers(
                       parse_text(CONFIG.replace("ca.crt", "missing.crt")), {})
    asyncio.run(main())


def test_read_only_client_tls(pki):
    from sme_modbus_sim.protocol.client import ClientError
    from sme_modbus_sim.scan import make_client

    async def body(srv, events, units):
        port = srv.bound_port
        t = {"type": "tls", "host": "127.0.0.1", "port": port, "ca": pki["ca"],
             "certificate": pki["viewer"][0], "private_key": pki["viewer"][1]}
        async with make_client(t, timeout=2, rate=1000) as c:
            assert c.endpoint == f"tls 127.0.0.1:{port}"
            assert await c.read_holding_registers(1, 0, 2) == [100, 101]
        with pytest.raises(ClientError, match="TLS handshake"):
            async with make_client({**t, "ca": pki["other_ca"]}, timeout=2) as c:
                pass
    run_tls(pki, body, roles={"viewer": "read"})
    with pytest.raises(ValueError, match="only apply to type tls"):
        make_client({"type": "tcp", "host": "h", "ca": "ca.crt"})
    with pytest.raises(ValueError, match="cannot load TLS"):
        make_client({"type": "tls", "host": "h", "ca": "missing-ca.crt"})
    assert make_client({"type": "tls", "host": "h"}).port == 802
