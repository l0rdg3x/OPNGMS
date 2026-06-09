import socket

import pytest

from app.connectors.opnsense.url_safety import UnsafeUrlError, validate_base_url


def test_public_ip_allowed():
    ip, host, port = validate_base_url("https://203.0.113.10")
    assert ip == "203.0.113.10"
    assert host == "203.0.113.10"


def test_private_ip_allowed():
    # RFC1918 is ALLOWED (firewall on management network)
    ip, host, port = validate_base_url("https://10.0.0.5")
    assert ip == "10.0.0.5"


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1",        # loopback
        "https://169.254.169.254",  # metadata cloud (link-local)
        "https://0.0.0.0",          # unspecified
        "https://[::1]",            # loopback v6
    ],
)
def test_dangerous_ip_blocked(url):
    with pytest.raises(UnsafeUrlError):
        validate_base_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://203.0.113.10",            # non-https
        "https://user:pass@203.0.113.10", # userinfo
        "ftp://203.0.113.10",             # scheme
    ],
)
def test_bad_scheme_or_userinfo_blocked(url):
    with pytest.raises(UnsafeUrlError):
        validate_base_url(url)


def test_hostname_resolving_to_loopback_blocked(monkeypatch):
    # DNS-rebinding-style: a hostname that resolves to 127.0.0.1 must be blocked
    def fake_getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(
        "app.connectors.opnsense.url_safety.socket.getaddrinfo", fake_getaddrinfo
    )
    with pytest.raises(UnsafeUrlError):
        validate_base_url("https://evil.example.test")
