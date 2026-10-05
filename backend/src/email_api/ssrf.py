"""Keep customer-supplied SMTP hosts from reaching anything but the public internet.

A company admin (or an API key) supplies the host the worker connects to, and
the worker then speaks SMTP to it and hands over a password. Unchecked, that is
a server-side request forgery primitive: point it at 127.0.0.1:6379, at the
cloud metadata service, or at a database on the private network.

So, at save time, at test time and again on every send:

1. the host must be a public DNS name (no IP literals, no ``localhost`` or
   internal suffixes) on an allowed port whose TLS mode matches;
2. every address it resolves to must be globally routable;
3. the connection is made to the address that passed the check — not by
   resolving the name a second time — while TLS still verifies the certificate
   against the *hostname*. Checking one resolution and connecting with another
   is exactly the gap DNS rebinding uses.

There is no switch to turn certificate verification off. ``SMTP_TLS_VERIFY`` is
only honoured by the legacy sender until it is removed.
"""
from __future__ import annotations

import ipaddress
import os
import re
import smtplib
import socket
import ssl
from typing import Callable, Dict, List, Optional

from .constants import SECURITY_IMPLICIT_TLS, SECURITY_STARTTLS

_BASE_PORTS: Dict[int, str] = {465: SECURITY_IMPLICIT_TLS, 587: SECURITY_STARTTLS}

# Suffixes that never name a public mail server.
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".lan",
                     ".home.arpa", ".corp", ".private")
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class DestinationNotAllowed(ValueError):
    """The SMTP destination failed a policy check. ``code`` is machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def allowed_ports() -> Dict[int, str]:
    """465/587, plus operator-approved extras from ``EMAIL_SMTP_EXTRA_PORTS``.

    Format: ``2465:implicit_tls,2587:starttls_required``. Port 25 is refused even
    here: it is server-to-server relay, not authenticated submission.
    """
    ports = dict(_BASE_PORTS)
    for part in os.getenv("EMAIL_SMTP_EXTRA_PORTS", "").split(","):
        port, _, mode = part.strip().partition(":")
        if port.isdigit() and int(port) != 25 and mode in (SECURITY_IMPLICIT_TLS, SECURITY_STARTTLS):
            ports[int(port)] = mode
    return ports


def validate_host(host: Optional[str]) -> str:
    """Return the normalised hostname, or raise ``DestinationNotAllowed``."""
    name = (host or "").strip().lower().rstrip(".")
    if not name:
        raise DestinationNotAllowed("host_required", "SMTP host is required")
    try:
        ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        pass
    else:
        raise DestinationNotAllowed("host_ip_literal", "Use the mail server's hostname, not an IP address")
    if len(name) > 253 or "." not in name:
        raise DestinationNotAllowed("host_invalid", "SMTP host must be a fully qualified domain name")
    if name == "localhost" or name.endswith(_BLOCKED_SUFFIXES):
        raise DestinationNotAllowed("host_internal", "SMTP host must be a public mail server")
    if not all(_LABEL.match(label) for label in name.split(".")):
        raise DestinationNotAllowed("host_invalid", "SMTP host is not a valid hostname")
    for suffix in filter(None, (s.strip().lower() for s in
                                os.getenv("EMAIL_SMTP_HOST_DENYLIST", "").split(","))):
        if name == suffix.lstrip(".") or name.endswith("." + suffix.lstrip(".")):
            raise DestinationNotAllowed("host_denied", "This SMTP host is not allowed")
    return name


def validate_port(port, security: Optional[str] = None) -> str:
    """Return the TLS mode for ``port``, or raise ``DestinationNotAllowed``.

    ``security`` may be omitted, in which case the port decides; if given it must
    agree with the port, so implicit TLS is never attempted on a STARTTLS port.
    """
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise DestinationNotAllowed("port_invalid", "SMTP port must be a number")
    mode = allowed_ports().get(port)
    if mode is None:
        raise DestinationNotAllowed(
            "port_not_allowed",
            "SMTP port must be 465 (implicit TLS) or 587 (STARTTLS)")
    if security and security != mode:
        raise DestinationNotAllowed(
            "security_mismatch", f"Port {port} uses {mode}, not {security}")
    return mode


def ip_allowed(ip: str) -> bool:
    """Whether a resolved address is a public unicast destination."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    # is_global already excludes private, loopback, link-local, CGNAT
    # (100.64/10 — Alibaba's metadata is 100.100.100.200), benchmarking and the
    # rest of the IANA special-purpose registry. The explicit checks below cover
    # what is_global does not reject on every Python version.
    if not addr.is_global:
        return False
    return not (addr.is_multicast or addr.is_reserved or addr.is_unspecified
                or addr.is_loopback or addr.is_link_local or addr.is_private)


Resolver = Callable[[str, int], List[str]]


def _system_resolve(host: str, port: int) -> List[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen, out = set(), []
    for *_rest, sockaddr in infos:
        ip = sockaddr[0]
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def resolve_public_ips(host: str, port: int, resolver: Optional[Resolver] = None) -> List[str]:
    """Resolve ``host`` and require EVERY address to be public.

    All, not any: a name that resolves to one public and one private address
    would otherwise let the connection land on the private one.
    """
    try:
        ips = (resolver or _system_resolve)(host, port)
    except socket.gaierror as e:
        raise DestinationNotAllowed("host_unresolvable", f"Could not resolve {host}: {e}") from e
    if not ips:
        raise DestinationNotAllowed("host_unresolvable", f"{host} has no address")
    bad = [ip for ip in ips if not ip_allowed(ip)]
    if bad:
        raise DestinationNotAllowed(
            "destination_not_allowed", f"{host} resolves to a non-public address")
    return ips


def validate_smtp_endpoint(host, port, security: Optional[str] = None,
                           resolver: Optional[Resolver] = None) -> Dict[str, object]:
    """Run every check. Returns ``{host, port, security, ips}``."""
    name = validate_host(host)
    mode = validate_port(port, security)
    ips = resolve_public_ips(name, int(port), resolver)
    return {"host": name, "port": int(port), "security": mode, "ips": ips}


def tls_context() -> ssl.SSLContext:
    """Verified TLS, 1.2 or newer. Deliberately not configurable."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class _PinnedSocket(smtplib.SMTP):
    """Connect to a pre-vetted address while keeping the hostname for TLS.

    ``smtplib`` stores the name passed to ``connect()`` as ``self._host`` and
    uses it as the TLS ``server_hostname`` for both SMTP_SSL and starttls(), so
    certificate verification still targets the hostname. Only the socket's
    destination is swapped for the address that passed ``ip_allowed``.
    """
    _pinned_ip: Optional[str] = None

    def _get_socket(self, host, port, timeout):
        if not self._pinned_ip:
            raise DestinationNotAllowed("destination_not_allowed", "no vetted address to connect to")
        if timeout is not None and not timeout:
            raise ValueError("Non-blocking socket (timeout=0) is not supported")
        return socket.create_connection((self._pinned_ip, port), timeout, self.source_address)


class PinnedSMTP(_PinnedSocket):
    def __init__(self, host: str, port: int, *, pinned_ip: str, timeout: float):
        self._pinned_ip = pinned_ip
        super().__init__(host, port, timeout=timeout)


class PinnedSMTP_SSL(smtplib.SMTP_SSL, _PinnedSocket):
    # MRO: SMTP_SSL._get_socket wraps whatever super()._get_socket returns,
    # which resolves to _PinnedSocket._get_socket before smtplib.SMTP's.
    def __init__(self, host: str, port: int, *, pinned_ip: str, timeout: float,
                 context: ssl.SSLContext):
        self._pinned_ip = pinned_ip
        smtplib.SMTP_SSL.__init__(self, host, port, timeout=timeout, context=context)
