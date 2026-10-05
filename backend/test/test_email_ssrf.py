"""Customer-supplied SMTP hosts may only reach public mail servers."""
from unittest.mock import MagicMock

import pytest

from src.email_api import ssrf
from src.email_api.ssrf import DestinationNotAllowed


@pytest.mark.parametrize("host,code", [
    ("", "host_required"),
    ("127.0.0.1", "host_ip_literal"),
    ("[::1]", "host_ip_literal"),
    ("169.254.169.254", "host_ip_literal"),
    ("localhost", "host_invalid"),
    ("mail", "host_invalid"),
    ("redis.localhost", "host_internal"),
    ("printer.local", "host_internal"),
    ("metadata.google.internal", "host_internal"),
    ("bad_host.example.com", "host_invalid"),
    ("-lead.example.com", "host_invalid"),
])
def test_rejected_hosts(host, code):
    with pytest.raises(DestinationNotAllowed) as e:
        ssrf.validate_host(host)
    assert e.value.code == code


def test_host_normalised():
    assert ssrf.validate_host(" Mail.ABC.example. ") == "mail.abc.example"


def test_denylist(monkeypatch):
    monkeypatch.setenv("EMAIL_SMTP_HOST_DENYLIST", "corp.example.com")
    with pytest.raises(DestinationNotAllowed):
        ssrf.validate_host("smtp.corp.example.com")
    assert ssrf.validate_host("smtp.example.com") == "smtp.example.com"


@pytest.mark.parametrize("port,security,expected", [
    (465, None, "implicit_tls"),
    (587, None, "starttls_required"),
    ("587", "starttls_required", "starttls_required"),
])
def test_allowed_ports(port, security, expected):
    assert ssrf.validate_port(port, security) == expected


@pytest.mark.parametrize("port,security,code", [
    (25, None, "port_not_allowed"),
    (2525, None, "port_not_allowed"),
    (6379, None, "port_not_allowed"),
    (465, "starttls_required", "security_mismatch"),
    ("x", None, "port_invalid"),
])
def test_refused_ports(port, security, code):
    with pytest.raises(DestinationNotAllowed) as e:
        ssrf.validate_port(port, security)
    assert e.value.code == code


def test_extra_ports_never_include_25(monkeypatch):
    monkeypatch.setenv("EMAIL_SMTP_EXTRA_PORTS", "2465:implicit_tls,25:starttls_required")
    assert ssrf.validate_port(2465) == "implicit_tls"
    with pytest.raises(DestinationNotAllowed):
        ssrf.validate_port(25)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1",
    "169.254.169.254",          # AWS/GCP/Azure metadata
    "100.100.100.200",          # Alibaba metadata (CGNAT range)
    "0.0.0.0", "198.18.0.1", "224.0.0.1", "240.0.0.1",
    "::1", "fe80::1", "fd00:ec2::254", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
])
def test_non_public_addresses_refused(ip):
    assert not ssrf.ip_allowed(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
def test_public_addresses_allowed(ip):
    assert ssrf.ip_allowed(ip)


def test_every_resolved_address_must_be_public():
    mixed = lambda host, port: ["93.184.216.34", "10.0.0.7"]
    with pytest.raises(DestinationNotAllowed) as e:
        ssrf.resolve_public_ips("smtp.example.com", 587, resolver=mixed)
    assert e.value.code == "destination_not_allowed"
    assert ssrf.resolve_public_ips("smtp.example.com", 587,
                                   resolver=lambda h, p: ["93.184.216.34"]) == ["93.184.216.34"]


def test_unresolvable_host():
    import socket

    def fail(host, port):
        raise socket.gaierror("nope")
    with pytest.raises(DestinationNotAllowed) as e:
        ssrf.resolve_public_ips("nx.example.com", 587, resolver=fail)
    assert e.value.code == "host_unresolvable"


def test_validate_smtp_endpoint_combines_checks():
    out = ssrf.validate_smtp_endpoint("smtp.example.com", 465,
                                      resolver=lambda h, p: ["93.184.216.34"])
    assert out == {"host": "smtp.example.com", "port": 465,
                   "security": "implicit_tls", "ips": ["93.184.216.34"]}


class _Stop(Exception):
    pass


def test_pinned_smtp_connects_to_vetted_ip(monkeypatch):
    calls = []

    def fake_create(addr, timeout, source):
        calls.append(addr)
        raise _Stop()
    monkeypatch.setattr(ssrf.socket, "create_connection", fake_create)
    with pytest.raises(_Stop):
        ssrf.PinnedSMTP("smtp.example.com", 587, pinned_ip="93.184.216.34", timeout=5)
    assert calls == [("93.184.216.34", 587)]


def test_pinned_ssl_verifies_certificate_against_hostname(monkeypatch):
    monkeypatch.setattr(ssrf.socket, "create_connection", lambda addr, t, s: MagicMock())
    ctx = MagicMock()
    ctx.wrap_socket.side_effect = _Stop()
    with pytest.raises(_Stop):
        ssrf.PinnedSMTP_SSL("smtp.example.com", 465, pinned_ip="93.184.216.34",
                            timeout=5, context=ctx)
    assert ctx.wrap_socket.call_args.kwargs["server_hostname"] == "smtp.example.com"


def test_tls_context_always_verifies():
    import ssl
    ctx = ssrf.tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
