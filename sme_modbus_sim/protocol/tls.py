"""Modbus/TCP Security (Modbus.org "MB-TCP-Security" v36, 2021-07-30) helpers.

* ``server_context`` / ``client_context``: TLS 1.2 or newer (R-01, R-34), mutual authentication with
  X.509v3 certificates (R-07, R-08), forward-secret AEAD cipher suites for TLS 1.2 (R-14, R-36, R-37).
* ``cert_role``: the role from the client certificate's Modbus role extension, OID
  1.3.6.1.4.1.50316.802.1, a UTF8String (R-21, R-22). The ``ssl`` module does not expose unknown
  extensions, so a minimal DER walk reads it from ``getpeercert(binary_form=True)``.
* ``Authorizer``: role + function code -> allowed (R-16, R-24..R-28); a refused request answers
  exception 01 Illegal Function (R-31).
"""
from __future__ import annotations

import ssl
from pathlib import Path
from typing import Iterable, Mapping, Optional, Union

ROLE_OID = "1.3.6.1.4.1.50316.802.1"
READ_FUNCTION_CODES = frozenset({0x01, 0x02, 0x03, 0x04, 0x2B})
TLS12_CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20:!aNULL:!eNULL"
HANDSHAKE_TIMEOUT_S = 10.0


def encode_oid(dotted: str) -> bytes:
    parts = [int(p) for p in dotted.split(".")]
    out = bytearray([40 * parts[0] + parts[1]])
    for n in parts[2:]:
        chunk = [n & 0x7F]
        n >>= 7
        while n:
            chunk.append(0x80 | (n & 0x7F))
            n >>= 7
        out.extend(reversed(chunk))
    return bytes(out)


_ROLE_OID_DER = encode_oid(ROLE_OID)


class _DerError(ValueError):
    pass


def _tlv(buf: bytes, pos: int) -> tuple[int, int, int]:
    """Return (tag, content start, content end) of the DER element at ``pos``."""
    if pos + 2 > len(buf):
        raise _DerError("truncated")
    tag, n = buf[pos], buf[pos + 1]
    pos += 2
    if n & 0x80:
        k = n & 0x7F
        if not 1 <= k <= 4 or pos + k > len(buf):
            raise _DerError("bad length")
        n = int.from_bytes(buf[pos:pos + k], "big")
        pos += k
    if pos + n > len(buf):
        raise _DerError("truncated")
    return tag, pos, pos + n


def _children(buf: bytes, start: int, end: int):
    pos = start
    while pos < end:
        tag, s, e = _tlv(buf, pos)
        yield tag, s, e
        pos = e


def cert_roles(der: bytes) -> list[str]:
    """All values of the Modbus role extension in a DER certificate (normally zero or one)."""
    tag, s, e = _tlv(der, 0)  # Certificate ::= SEQUENCE { tbsCertificate, ... }
    if tag != 0x30:
        raise _DerError("not a certificate")
    tag, s, e = _tlv(der, s)  # TBSCertificate ::= SEQUENCE
    if tag != 0x30:
        raise _DerError("not a certificate")
    roles: list[str] = []
    for tag, cs, ce in _children(der, s, e):
        if tag != 0xA3:  # extensions [3] EXPLICIT
            continue
        stag, ss, se = _tlv(der, cs)
        for etag, es, ee in _children(der, ss, se):  # Extension ::= SEQUENCE { extnID, critical?, extnValue }
            items = list(_children(der, es, ee))
            if not items or items[0][0] != 0x06 or der[items[0][1]:items[0][2]] != _ROLE_OID_DER:
                continue
            octet = items[-1]
            if octet[0] != 0x04:
                raise _DerError("role extension without OCTET STRING")
            vtag, vs, ve = _tlv(der, octet[1])
            if vtag != 0x0C:  # UTF8String (R-22)
                raise _DerError("role is not a UTF8String")
            roles.append(der[vs:ve].decode("utf-8"))
    return roles


def cert_role(der: Optional[bytes]) -> Optional[str]:
    """The role of a client certificate; None when absent, repeated (R-65: one role) or unreadable."""
    if not der:
        return None
    try:
        roles = cert_roles(der)
    except (_DerError, UnicodeDecodeError):
        return None
    return roles[0] if len(roles) == 1 else None


def _path(p: Optional[str], base_dir: Optional[Path]) -> Optional[str]:
    if p is None:
        return None
    path = Path(p).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    return str(path)


def _harden(ctx: ssl.SSLContext) -> None:
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers(TLS12_CIPHERS)  # TLS 1.3 suites are all AEAD and stay enabled
    ctx.options |= ssl.OP_NO_COMPRESSION  # R-59


def server_context(certificate: str, private_key: Optional[str], ca: Optional[str] = None, *,
                   client_auth: bool = True, crl: Optional[str] = None,
                   base_dir: Optional[Path] = None) -> ssl.SSLContext:
    """TLS server context. ``client_auth``: require and verify a client certificate (R-07, R-10)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    _harden(ctx)
    ctx.load_cert_chain(_path(certificate, base_dir), _path(private_key, base_dir))
    if ca is not None:
        ctx.load_verify_locations(cafile=_path(ca, base_dir))
        ctx.verify_mode = ssl.CERT_REQUIRED if client_auth else ssl.CERT_OPTIONAL
    else:
        ctx.verify_mode = ssl.CERT_NONE
    if crl is not None:
        ctx.load_verify_locations(cafile=_path(crl, base_dir))
        ctx.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
    return ctx


def client_context(ca: Optional[str] = None, certificate: Optional[str] = None,
                   private_key: Optional[str] = None, *, base_dir: Optional[Path] = None) -> ssl.SSLContext:
    """TLS client context. The server certificate is verified against ``ca`` (no host name check:
    the specification defines none); without ``ca`` it is not verified at all."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    _harden(ctx)
    ctx.check_hostname = False
    if ca is not None:
        ctx.load_verify_locations(cafile=_path(ca, base_dir))
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:
        ctx.verify_mode = ssl.CERT_NONE
    if certificate is not None:
        ctx.load_cert_chain(_path(certificate, base_dir), _path(private_key, base_dir))
    return ctx


RoleRights = Union[str, Iterable[int]]


class Authorizer:
    """Role -> allowed function codes. ``rights`` per role: ``"all"``, ``"read"`` (01-04, 2B/0E)
    or a list of function codes. A role that is not listed, and a certificate without a role,
    may only read (ModbusSim's default for the vendor-defined rights database, R-24)."""

    def __init__(self, roles: Mapping[str, RoleRights]) -> None:
        self.roles = {name: self._fcs(r) for name, r in roles.items()}

    @staticmethod
    def _fcs(rights: RoleRights) -> Optional[frozenset]:
        if rights == "all":
            return None
        if rights == "read":
            return READ_FUNCTION_CODES
        return frozenset(int(fc) for fc in rights)  # type: ignore[union-attr]

    def allowed(self, role: Optional[str], fc: int) -> bool:
        fcs = self.roles.get(role, READ_FUNCTION_CODES) if role is not None else READ_FUNCTION_CODES
        return fcs is None or fc in fcs
