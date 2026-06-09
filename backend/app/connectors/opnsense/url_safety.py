import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeUrlError(Exception):
    """base_url rejected by the SSRF guard."""


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # BLOCK never-legitimate targets; ALLOW private IPs (RFC1918) for mgmt firewalls.
    return (
        ip.is_loopback        # 127.0.0.0/8, ::1
        or ip.is_link_local   # 169.254.0.0/16 (metadata cloud!), fe80::/10
        or ip.is_unspecified  # 0.0.0.0, ::
        or ip.is_multicast
        or ip.is_reserved
    )


def _resolve(host: str, port: int) -> set[str]:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return {info[4][0] for info in infos}


def validate_base_url(base_url: str) -> tuple[str, str, int | None]:
    """Validate and resolve base_url against SSRF. Returns (pinned_ip, hostname, port|None).

    Raises UnsafeUrlError if: scheme != https, userinfo present, host missing,
    resolution failed, or ANY resolved address is loopback/link-local/
    unspecified/multicast/reserved. Pins the first validated IP (anti DNS-rebinding).
    """
    parts = urlsplit(base_url)
    if parts.scheme != "https":
        raise UnsafeUrlError("only https allowed")
    if parts.username or parts.password:
        raise UnsafeUrlError("credentials in the URL not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("missing host")
    port = parts.port  # None if not specified
    try:
        addrs = _resolve(host, port or 443)
    except socket.gaierror as exc:
        raise UnsafeUrlError("DNS resolution failed") from exc
    if not addrs:
        raise UnsafeUrlError("no resolved address")
    for raw in addrs:
        if _blocked(ipaddress.ip_address(raw)):
            raise UnsafeUrlError("destination address not allowed")
    pinned = sorted(addrs)[0]  # all validated; pin the first (deterministic)
    return pinned, host, port
