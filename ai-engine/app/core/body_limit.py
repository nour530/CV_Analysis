from __future__ import annotations

import json
from typing import Any

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.audit import audit_event

REQUEST_TOO_LARGE_CODE = "request_too_large"


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _request_id(scope: Scope) -> str | None:
    state = scope.get("state")
    if isinstance(state, dict):
        value = state.get("request_id")
        if isinstance(value, str):
            return value
    return None


def _client_host(scope: Scope) -> str | None:
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        host = client[0]
        if isinstance(host, str):
            return host
    return None


class BodySizeLimitMiddleware:
    """Reject HTTP request bodies above a configurable byte limit.

    The middleware first trusts a valid ``Content-Length`` header for an early
    rejection. Because clients can omit or lie about that header, it also counts
    bytes as the application reads them. A stream that exceeds the limit is
    stopped and the application's response is replaced with a JSON ``413``.
    """

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_body_bytes <= 0:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        declared_length = _parse_content_length(headers.get("content-length"))
        if declared_length is not None and declared_length > self.max_body_bytes:
            await self._send_rejection(
                scope,
                send,
                reason="content_length",
                observed_bytes=declared_length,
            )
            return

        received = 0
        rejected = False
        rejection_reason = "stream"
        observed_bytes = 0
        rejection_sent = False

        async def limited_receive() -> Message:
            nonlocal received, rejected, observed_bytes
            message = await receive()
            if message["type"] == "http.request" and not rejected:
                body = message.get("body", b"")
                received += len(body)
                if received > self.max_body_bytes:
                    rejected = True
                    observed_bytes = received
                    # Stop the application from reading further body chunks.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal rejection_sent
            if rejected:
                if message["type"] == "http.response.start":
                    if not rejection_sent:
                        rejection_sent = True
                        await self._send_rejection(
                            scope,
                            send,
                            reason=rejection_reason,
                            observed_bytes=observed_bytes,
                        )
                    return
                if message["type"] == "http.response.body":
                    return
            await send(message)

        await self.app(scope, limited_receive, guarded_send)

    async def _send_rejection(
        self,
        scope: Scope,
        send: Send,
        *,
        reason: str,
        observed_bytes: int,
    ) -> None:
        request_id = _request_id(scope)
        audit_event(
            "security.request_body.rejected",
            "failure",
            request_id=request_id,
            client_ip=_client_host(scope),
            metadata={
                "reason": reason,
                "limit_bytes": self.max_body_bytes,
                "observed_bytes": observed_bytes,
            },
        )
        payload: dict[str, Any] = {
            "error": {
                "code": REQUEST_TOO_LARGE_CODE,
                "message": (
                    "Request body exceeds the "
                    f"{self.max_body_bytes} byte limit."
                ),
                "details": [],
            },
            "request_id": request_id,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
