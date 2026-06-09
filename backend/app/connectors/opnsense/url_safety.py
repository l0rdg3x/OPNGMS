import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeUrlError(Exception):
    """base_url rifiutato dalla guardia SSRF."""


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # unwrap IPv4-mapped IPv6 (es. ::ffff:127.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # BLOCCA i target mai-legittimi; PERMETTE gli IP privati (RFC1918) per i firewall mgmt.
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
    """Valida e risolve base_url contro la SSRF. Ritorna (pinned_ip, hostname, port|None).

    Solleva UnsafeUrlError se: schema != https, userinfo presente, host mancante,
    risoluzione fallita, o QUALSIASI indirizzo risolto è loopback/link-local/
    unspecified/multicast/reserved. Pinna il primo IP validato (anti DNS-rebinding).
    """
    parts = urlsplit(base_url)
    if parts.scheme != "https":
        raise UnsafeUrlError("solo https consentito")
    if parts.username or parts.password:
        raise UnsafeUrlError("credenziali nell'URL non consentite")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("host mancante")
    port = parts.port  # None se non specificato
    try:
        addrs = _resolve(host, port or 443)
    except socket.gaierror as exc:
        raise UnsafeUrlError("risoluzione DNS fallita") from exc
    if not addrs:
        raise UnsafeUrlError("nessun indirizzo risolto")
    for raw in addrs:
        if _blocked(ipaddress.ip_address(raw)):
            raise UnsafeUrlError("indirizzo di destinazione non consentito")
    pinned = sorted(addrs)[0]  # tutti validati; pinna il primo (deterministico)
    return pinned, host, port
