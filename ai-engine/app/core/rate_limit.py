from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
)
from typing import TYPE_CHECKING, Protocol

from fastapi import Request

from app.api.errors import APIError
from app.core.audit import pseudonymize_identifier

if TYPE_CHECKING:
    from app.api.settings import APISettings

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network
LOGGER = logging.getLogger("jobmatch.rate_limit")

REDIS_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local member = ARGV[3]
local current = redis.call('TIME')
local now = tonumber(current[1]) + (tonumber(current[2]) / 1000000)
local cutoff = now - window
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_after = 1
  if oldest[2] ~= nil then
    retry_after = math.max(1, math.ceil(tonumber(oldest[2]) + window - now))
  end
  redis.call('PEXPIRE', key, math.max(1000, math.ceil(window * 1000) + 1000))
  return {0, retry_after, 0}
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, math.max(1000, math.ceil(window * 1000) + 1000))
return {1, 0, limit - count - 1}
"""


class RateLimitBackendError(RuntimeError):
    """Raised when shared rate-limit storage is unavailable."""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int
    remaining: int


@dataclass(slots=True)
class _Bucket:
    events: deque[float]
    window_seconds: int
    last_seen: float


class SlidingWindowRateLimiter:
    def __init__(self, *, max_keys: int = 20_000) -> None:
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max(1, max_keys)
        self._cleanup_interval = 60.0
        self._next_cleanup = time.monotonic() + self._cleanup_interval

    @property
    def key_count(self) -> int:
        with self._lock:
            return len(self._buckets)

    def configure(self, *, max_keys: int) -> None:
        with self._lock:
            self._max_keys = max(1, max_keys)
            self._enforce_capacity()

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        if limit <= 0:
            return RateLimitDecision(allowed=True, retry_after=0, remaining=limit)
        window_seconds = max(1, window_seconds)
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(deque(), window_seconds, now)
                self._buckets[key] = bucket
            bucket.window_seconds = window_seconds
            bucket.last_seen = now
            while bucket.events and bucket.events[0] <= cutoff:
                bucket.events.popleft()
            if len(bucket.events) >= limit:
                retry_after = max(1, int(bucket.events[0] + window_seconds - now) + 1)
                self._buckets.move_to_end(key)
                self._maybe_cleanup(now)
                return RateLimitDecision(
                    allowed=False,
                    retry_after=retry_after,
                    remaining=0,
                )
            bucket.events.append(now)
            self._buckets.move_to_end(key)
            self._maybe_cleanup(now)
            return RateLimitDecision(
                allowed=True,
                retry_after=0,
                remaining=max(0, limit - len(bucket.events)),
            )

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    def _maybe_cleanup(self, now: float) -> None:
        if len(self._buckets) > self._max_keys:
            self._enforce_capacity()
        if now < self._next_cleanup:
            return
        stale_keys = [
            key
            for key, bucket in self._buckets.items()
            if bucket.last_seen <= now - bucket.window_seconds
        ]
        for key in stale_keys:
            self._buckets.pop(key, None)
        self._enforce_capacity()
        self._next_cleanup = now + self._cleanup_interval

    def _enforce_capacity(self) -> None:
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)


class RateLimitBackend(Protocol):
    name: str

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        ...

    def reset(self, key: str | None = None) -> None:
        ...

    def ping(self) -> bool:
        ...

    def close(self) -> None:
        ...


class MemoryRateLimitBackend:
    name = "memory"

    def __init__(self, limiter: SlidingWindowRateLimiter | None = None) -> None:
        self.limiter = limiter or SlidingWindowRateLimiter()

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        return self.limiter.check(key, limit=limit, window_seconds=window_seconds)

    def reset(self, key: str | None = None) -> None:
        self.limiter.reset(key)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None


class RedisRateLimitBackend:
    name = "redis"

    def __init__(
        self,
        client: object,
        *,
        key_prefix: str,
    ) -> None:
        self._client = client
        self._key_prefix = key_prefix

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        key_prefix: str,
        connect_timeout_seconds: float,
        socket_timeout_seconds: float,
        health_check_interval_seconds: int,
        max_connections: int,
    ) -> RedisRateLimitBackend:
        try:
            import redis
        except ImportError as exc:
            raise RateLimitBackendError(
                "The redis package is required when RATE_LIMIT_BACKEND=redis."
            ) from exc
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=connect_timeout_seconds,
            socket_timeout=socket_timeout_seconds,
            health_check_interval=health_check_interval_seconds,
            max_connections=max_connections,
            retry_on_timeout=False,
        )
        return cls(client, key_prefix=key_prefix)

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        if limit <= 0:
            return RateLimitDecision(allowed=True, retry_after=0, remaining=limit)
        window_seconds = max(1, window_seconds)
        try:
            result = self._client.eval(  # type: ignore[attr-defined]
                REDIS_RATE_LIMIT_SCRIPT,
                1,
                f"{self._key_prefix}{key}",
                window_seconds,
                limit,
                uuid.uuid4().hex,
            )
        except Exception as exc:
            raise RateLimitBackendError(
                "Shared rate-limit storage is unavailable."
            ) from exc
        if not isinstance(result, (list, tuple)) or len(result) < 3:
            raise RateLimitBackendError("Shared rate-limit storage returned invalid data.")
        return RateLimitDecision(
            allowed=bool(int(result[0])),
            retry_after=max(0, int(result[1])),
            remaining=max(0, int(result[2])),
        )

    def reset(self, key: str | None = None) -> None:
        try:
            if key is not None:
                self._client.delete(f"{self._key_prefix}{key}")  # type: ignore[attr-defined]
                return
            for redis_key in self._client.scan_iter(  # type: ignore[attr-defined]
                match=f"{self._key_prefix}*",
                count=500,
            ):
                self._client.delete(redis_key)  # type: ignore[attr-defined]
        except Exception as exc:
            raise RateLimitBackendError(
                "Shared rate-limit storage is unavailable."
            ) from exc

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())  # type: ignore[attr-defined]
        except Exception:
            return False

    def close(self) -> None:
        try:
            self._client.close()  # type: ignore[attr-defined]
        except Exception:
            return None


class RateLimiter:
    def __init__(self) -> None:
        self._backend: RateLimitBackend = MemoryRateLimitBackend()
        self._failure_mode = "closed"
        self._lock = threading.Lock()

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def failure_mode(self) -> str:
        return self._failure_mode

    def configure(
        self,
        *,
        backend: RateLimitBackend | None = None,
        max_keys: int | None = None,
        failure_mode: str = "closed",
    ) -> None:
        with self._lock:
            previous = self._backend
            if backend is not None:
                self._backend = backend
            elif isinstance(previous, MemoryRateLimitBackend) and max_keys is not None:
                previous.limiter.configure(max_keys=max_keys)
            self._failure_mode = failure_mode
        if backend is not None and previous is not backend:
            previous.close()

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        try:
            return self._backend.check(key, limit=limit, window_seconds=window_seconds)
        except RateLimitBackendError as exc:
            LOGGER.error(
                '{"component":"rate_limit","event":"backend_unavailable",'
                '"backend":"%s","error_type":"%s"}',
                self._backend.name,
                type(exc).__name__,
            )
            if self._failure_mode == "open":
                return RateLimitDecision(allowed=True, retry_after=0, remaining=0)
            raise

    def reset(self, key: str | None = None) -> None:
        try:
            self._backend.reset(key)
        except RateLimitBackendError:
            LOGGER.warning(
                '{"component":"rate_limit","event":"reset_failed","backend":"%s"}',
                self._backend.name,
            )

    def ping(self) -> bool:
        return self._backend.ping()

    def close(self) -> None:
        self._backend.close()


RATE_LIMITER = RateLimiter()


def make_rate_limit_key(scope: str, identity: str) -> str:
    return f"{scope}:{pseudonymize_identifier(identity)}"


def _parse_ip(value: str) -> IPAddress | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        address = ip_address(candidate)
    except ValueError:
        return None
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _is_trusted(address: IPAddress, networks: tuple[IPNetwork, ...]) -> bool:
    return any(address in network for network in networks)


def client_ip(
    request: Request,
    trusted_proxy_networks: tuple[IPNetwork, ...] = (),
) -> str:
    peer = request.client.host if request.client and request.client.host else "unknown"
    peer_address = _parse_ip(peer)
    if (
        peer_address is None
        or not trusted_proxy_networks
        or not _is_trusted(peer_address, trusted_proxy_networks)
    ):
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    forwarded_addresses = [
        address
        for entry in forwarded.split(",")
        if (address := _parse_ip(entry)) is not None
    ]
    for address in reversed(forwarded_addresses):
        if not _is_trusted(address, trusted_proxy_networks):
            return str(address)
    if forwarded_addresses:
        return str(forwarded_addresses[0])
    return peer


def enforce_rate_limit(
    request: Request,
    *,
    scope: str,
    limit: int,
    window_seconds: int,
    discriminator: str | None = None,
    trusted_proxy_networks: tuple[IPNetwork, ...] = (),
) -> RateLimitDecision:
    identity = discriminator or client_ip(request, trusted_proxy_networks)
    try:
        decision = RATE_LIMITER.check(
            make_rate_limit_key(scope, identity),
            limit=limit,
            window_seconds=window_seconds,
        )
    except RateLimitBackendError as exc:
        raise APIError(
            status_code=503,
            code="rate_limit_unavailable",
            message=(
                "Request protection is temporarily unavailable. Please try again later."
            ),
            headers={"Retry-After": "5"},
        ) from exc
    if not decision.allowed:
        raise APIError(
            status_code=429,
            code="rate_limit_exceeded",
            message="Too many requests. Please wait before trying again.",
            headers={"Retry-After": str(decision.retry_after)},
        )
    return decision


def configure_rate_limiter(
    *,
    max_keys: int | None = None,
    settings: APISettings | None = None,
) -> None:
    if settings is None:
        RATE_LIMITER.configure(
            backend=MemoryRateLimitBackend(
                SlidingWindowRateLimiter(max_keys=max_keys or 20_000)
            )
        )
        return
    if settings.rate_limit_backend == "redis":
        if settings.redis_url is None:
            raise RateLimitBackendError("REDIS_URL is required for the Redis backend.")
        backend: RateLimitBackend = RedisRateLimitBackend.from_url(
            settings.redis_url,
            key_prefix=settings.redis_key_prefix,
            connect_timeout_seconds=settings.redis_connect_timeout_seconds,
            socket_timeout_seconds=settings.redis_socket_timeout_seconds,
            health_check_interval_seconds=settings.redis_health_check_interval_seconds,
            max_connections=settings.redis_max_connections,
        )
    else:
        backend = MemoryRateLimitBackend(
            SlidingWindowRateLimiter(max_keys=settings.rate_limit_max_keys)
        )
    RATE_LIMITER.configure(
        backend=backend,
        failure_mode=settings.rate_limit_redis_failure_mode,
    )


def reset_rate_limits() -> None:
    RATE_LIMITER.reset()
