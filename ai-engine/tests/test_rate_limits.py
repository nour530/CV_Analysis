from __future__ import annotations

from ipaddress import ip_network

import pytest
from starlette.requests import Request

from app.api.settings import APIConfigurationError, APISettings
from app.core.rate_limit import SlidingWindowRateLimiter, client_ip


def _request(peer: str, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    return Request(
        {
            "type": "http",
            "headers": headers,
            "client": (peer, 54321),
        }
    )


def test_client_ip_ignores_forwarded_header_from_untrusted_peer():
    request = _request("203.0.113.5", "198.51.100.7")

    assert client_ip(request, (ip_network("10.0.0.0/8"),)) == "203.0.113.5"


def test_client_ip_accepts_forwarded_header_from_trusted_proxy():
    request = _request("10.0.0.5", "203.0.113.9, 198.51.100.10")

    assert client_ip(request, (ip_network("10.0.0.0/8"),)) == "198.51.100.10"


def test_client_ip_skips_trusted_hops_from_right_to_left():
    request = _request("10.0.0.5", "198.51.100.10, 10.0.0.6")

    assert client_ip(request, (ip_network("10.0.0.0/8"),)) == "198.51.100.10"


def test_client_ip_falls_back_to_peer_without_forwarded_header():
    request = _request("10.0.0.5")

    assert client_ip(request, (ip_network("10.0.0.0/8"),)) == "10.0.0.5"


def test_client_ip_skips_invalid_forwarded_entries():
    request = _request("10.0.0.5", "not-an-ip, 198.51.100.10")

    assert client_ip(request, (ip_network("10.0.0.0/8"),)) == "198.51.100.10"


def test_trusted_proxy_settings_accept_addresses_and_cidrs():
    settings = APISettings.from_env(
        {"TRUSTED_PROXY_IPS": "127.0.0.1, 10.0.0.0/8, ::1"}
    )

    assert settings.trusted_proxy_networks == (
        ip_network("127.0.0.1/32"),
        ip_network("10.0.0.0/8"),
        ip_network("::1/128"),
    )


def test_trusted_proxy_settings_reject_invalid_entries():
    with pytest.raises(APIConfigurationError):
        APISettings.from_env({"TRUSTED_PROXY_IPS": "not-an-ip"})


def test_limiter_evicts_least_recently_used_keys_at_capacity():
    limiter = SlidingWindowRateLimiter(max_keys=2)
    assert limiter.check("a", limit=1, window_seconds=60).allowed
    assert limiter.check("b", limit=1, window_seconds=60).allowed
    assert limiter.key_count == 2

    assert limiter.check("c", limit=1, window_seconds=60).allowed
    assert limiter.key_count == 2
    assert limiter.check("a", limit=1, window_seconds=60).allowed


def test_limiter_prunes_stale_keys(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.core.rate_limit.time.monotonic", lambda: clock[0])
    limiter = SlidingWindowRateLimiter(max_keys=10)
    assert limiter.check("a", limit=1, window_seconds=1).allowed
    assert limiter.key_count == 1

    clock[0] = 61.0
    assert limiter.check("b", limit=1, window_seconds=1).allowed
    assert limiter.key_count == 1
