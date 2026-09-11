"""Bound the request body of the endpoints anyone can reach without a token.

Gate 5 bullet 3's property is that no server route materialises a whole request
body before something decides it is small enough. R-A8U closed
`POST /api/billing/webhooks/{provider}` — which did `await request.body()` — and
an independent review immediately pointed out that closing it by name had not
closed the class:

    POST /api/client-errors declares `payload: ClientErrorReport` and never
    touches `request.body()` at all. FastAPI still materialises and JSON-parses
    the entire body before the handler is entered, so the model's per-field
    `max_length` caps and the 60-per-minute IP rate limiter both run **after**
    the memory has been held. The route is unauthenticated by design, and the
    gateway allows `client_max_body_size 200m`.

That is the same sentence as the webhook — an anonymous caller decided how much
the process held — and it reaches four routes, not one. An upload guard built
around explicit reads cannot see any of them, because the read is FastAPI's.

**Why a middleware and not four handler rewrites.** Rewriting each handler to
take a raw `Request`, read it through a bounded seam and validate by hand moves
the model out of the signature, out of the OpenAPI schema and out of FastAPI's
422 handling — four times, on the login path. This runs before the application
is entered, leaves every signature alone, and is one seam for the class.

**Why only these paths.** A cap that applied everywhere would have to be larger
than `BULK_UPLOAD_MAX_SIZE_MB` to let the upload routes work, which would make
it no bound at all for a telemetry beacon. The routes here are the ones an
unauthenticated caller can reach, and each cap is sized from what its own model
can legitimately hold.

Authenticated routes are deliberately out of scope and are bounded by the upload
seams (`upload_streaming`) plus the gateway. A caller must get through
authentication first, and `test_upload_route_inventory.py` is what keeps the
upload surface itself honest.
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable, Mapping, MutableMapping, Sequence

from starlette.types import ASGIApp, Receive, Scope, Send


def _cap_bytes(kilobytes: int) -> int:
    return max(1, int(kilobytes)) * 1024


def public_body_caps(settings: object) -> Mapping[str, int]:
    """Path prefix -> maximum request body in bytes.

    Read from configuration rather than inlined, for the same reason the upload
    routes read theirs: a literal keeps every test green while ignoring the
    deployment.
    """
    telemetry = _cap_bytes(getattr(settings, "PUBLIC_TELEMETRY_MAX_BODY_SIZE_KB", 64))
    credentials = _cap_bytes(getattr(settings, "PUBLIC_AUTH_MAX_BODY_SIZE_KB", 16))
    webhook = _cap_bytes(getattr(settings, "WEBHOOK_MAX_BODY_SIZE_KB", 256))
    return {
        # Telemetry: `ClientErrorReport`'s own field caps total about 21 KB, and
        # `ContactRequest` is smaller again.
        "/api/client-errors": telemetry,
        "/api/contact": telemetry,
        # Credentials: an email and a password. 16 KB is four hundred times what
        # the model can hold and still four orders below the gateway.
        "/api/login": credentials,
        "/api/token": credentials,
        # The webhook already bounds itself in the handler through
        # `read_request_body_within_limit`. It is listed anyway so the two
        # cannot drift: if the handler is ever changed back, the body is still
        # bounded, and the cap is the same configured value.
        "/api/billing/webhooks": webhook,
    }


def cap_for(path: str, caps: Mapping[str, int]) -> int | None:
    """The cap that applies to `path`, or None when the path is not covered.

    Prefix matching, because `/api/billing/webhooks/{provider}` is one route per
    provider and `/api/login` is reached with and without a trailing slash. The
    longest matching prefix wins, so a more specific entry can tighten a
    broader one.
    """
    best: int | None = None
    best_length = -1
    for prefix, cap in caps.items():
        if path == prefix or path.startswith(f"{prefix}/"):
            if len(prefix) > best_length:
                best, best_length = cap, len(prefix)
    return best


class PublicBodyLimitMiddleware:
    """Pure ASGI, so it runs before FastAPI parses anything.

    A `BaseHTTPMiddleware` would not do: Starlette builds the `Request` and the
    body is consumed by the route handler downstream of it, which is exactly the
    materialisation this exists to prevent.

    The body is read here, counted, and replayed to the application. Buffering is
    what makes the replay possible and is safe only because every cap in the
    table is small; this must never be widened to the upload routes, whose whole
    design is not to hold the body.
    """

    def __init__(self, app: ASGIApp, caps: Mapping[str, int]) -> None:
        self.app = app
        self.caps = dict(caps)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        cap = cap_for(str(scope.get("path") or ""), self.caps)
        if cap is None:
            await self.app(scope, receive, send)
            return

        # A declared Content-Length over the cap is refused without reading a
        # byte. A *forged* or absent one buys nothing: the count below is of
        # bytes actually received, which is the only number a client cannot lie
        # about.
        declared = _declared_length(scope.get("headers") or ())
        if declared is not None and declared > cap:
            await _refuse(send, cap)
            return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                # Hand the disconnect on unchanged; the application decides.
                await self.app(scope, _replay(chunks, disconnected=True), send)
                return
            if message["type"] != "http.request":
                continue
            body = message.get("body", b"") or b""
            total += len(body)
            if total > cap:
                await _refuse(send, cap)
                return
            if body:
                chunks.append(body)
            if not message.get("more_body", False):
                break

        await self.app(scope, _replay(chunks), send)


def _declared_length(headers: Sequence[tuple[bytes, bytes]]) -> int | None:
    for key, value in headers:
        if key.lower() == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _replay(chunks: Sequence[bytes], *, disconnected: bool = False) -> Receive:
    """A `receive` that serves the buffered body once, then end-of-stream."""

    pending = list(chunks)
    served = False

    async def receive() -> MutableMapping[str, object]:
        nonlocal served
        if disconnected and served:
            return {"type": "http.disconnect"}
        if not served:
            served = True
            return {
                "type": "http.request",
                "body": b"".join(pending),
                "more_body": False,
            }
        return {"type": "http.disconnect"}

    return receive


async def _refuse(send: Send, cap: int) -> None:
    """413, shaped like every other upload refusal on this service.

    `UploadTooLargeError` is an `HTTPException` and cannot be raised here — no
    exception handler is mounted above this middleware — so the response is
    written directly, with the same status and the same wording.
    """

    payload = json.dumps(
        {
            "detail": (
                "Request body exceeds the maximum allowed size "
                f"({_human(cap)}) for this endpoint"
            )
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload, "more_body": False})


def _human(cap: int) -> str:
    """`0MB` is not a limit anybody can act on.

    `UploadTooLargeError` renders `max_size_bytes // (1024 * 1024)`, which is
    literally "0MB" for every cap below a megabyte — and every cap in this table
    is below a megabyte.
    """
    if cap >= 1024 * 1024:
        return f"{cap // (1024 * 1024)}MB"
    if cap >= 1024:
        return f"{cap // 1024}KB"
    return f"{cap} bytes"


def install(app: ASGIApp, settings: object) -> Callable[..., Awaitable[None]]:
    """Wrap `app`, returning the middleware. Kept separate so a test can build
    one without an application."""
    return PublicBodyLimitMiddleware(app, public_body_caps(settings))
