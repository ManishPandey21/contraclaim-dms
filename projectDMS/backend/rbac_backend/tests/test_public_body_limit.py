"""Gate 5 bullet 3, the half an upload guard cannot see.

R-A8U bounded `POST /api/billing/webhooks/{provider}`, which did
`await request.body()`. An independent review immediately pointed out that
closing it by name had not closed the class:

    `POST /api/client-errors` declares `payload: ClientErrorReport` and never
    touches `request.body()`. FastAPI materialises and JSON-parses the **whole**
    body before the handler is entered, so the model's per-field `max_length`
    caps and the 60-per-minute IP rate limiter both decide *after* the memory
    has been held. The route is unauthenticated by design and the gateway allows
    `client_max_body_size 200m`.

The route's own docstring claims it is "IP rate-limited and size-capped so it
cannot be abused as a write amplifier". The size cap was per field, after the
parse.

Four unauthenticated routes have that shape. `core/public_body_limit.py` is one
seam for all of them, and this module measures the property through the **real
ASGI stack** rather than against the middleware in isolation - because
"materialised before the handler" is a statement about where in the stack the
decision happens, and only a real request can show that.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from rbac_backend.core.config import settings
from rbac_backend.core.public_body_limit import (
    PublicBodyLimitMiddleware,
    cap_for,
    public_body_caps,
)

TELEMETRY_CAP = max(1, int(settings.PUBLIC_TELEMETRY_MAX_BODY_SIZE_KB)) * 1024
AUTH_CAP = max(1, int(settings.PUBLIC_AUTH_MAX_BODY_SIZE_KB)) * 1024


class _Report(BaseModel):
    #: No `max_length` here on purpose. The real models cap their fields, and
    #: that cap is exactly what decides too late - after the parse. This model
    #: accepts anything, so a 200 proves the middleware let it through and a 413
    #: proves the middleware stopped it, with nothing else in the way.
    message: str


def _app() -> FastAPI:
    """A miniature of the real mounting: a declared body model, no explicit read.

    `seen` records how much the *application* was given, which is the number the
    finding is about. A handler that is never entered saw nothing.
    """

    app = FastAPI()
    app.state.seen = []

    @app.post("/api/client-errors")
    async def report(payload: _Report) -> dict:
        app.state.seen.append(len(payload.message))
        return {"status": "recorded"}

    @app.post("/api/login")
    async def login(payload: _Report) -> dict:
        app.state.seen.append(len(payload.message))
        return {"status": "ok"}

    @app.post("/api/documents")
    async def upload(request: Request) -> dict:
        body = await request.body()
        app.state.seen.append(len(body))
        return {"bytes": len(body)}

    app.add_middleware(PublicBodyLimitMiddleware, caps=public_body_caps(settings))
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_app())


def _payload(size: int) -> bytes:
    """A JSON document whose serialised length is exactly `size`."""
    envelope = json.dumps({"message": ""}).encode("utf-8")
    filler = size - len(envelope)
    assert filler >= 0
    return json.dumps({"message": "a" * filler}).encode("utf-8")


# --------------------------------------------------------------------------- #
# The property, through the real stack
# --------------------------------------------------------------------------- #


def test_a_body_at_the_cap_is_accepted(client: TestClient) -> None:
    response = client.post(
        "/api/client-errors",
        content=_payload(TELEMETRY_CAP),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200, response.text


def test_a_body_one_byte_over_the_cap_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/client-errors",
        content=_payload(TELEMETRY_CAP + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413, response.text


def test_the_handler_never_sees_an_oversize_body(client: TestClient) -> None:
    """The distinction the fix is about.

    A 413 produced *after* FastAPI parsed the body would look identical from
    outside and would have paid the whole memory cost. The application records
    what it was given; on a refusal it must have been given nothing.
    """
    app = client.app
    client.post(
        "/api/client-errors",
        content=_payload(TELEMETRY_CAP * 4),
        headers={"content-type": "application/json"},
    )
    assert app.state.seen == [], (
        "the application was entered with an oversize body, so the limit bounds "
        "what is stored and not what the process holds"
    )


def test_a_forged_content_length_buys_nothing(client: TestClient) -> None:
    """The header says one byte; the transport delivers four caps' worth.

    `httpx` sets `content-length` from the content, so the forgery is written
    directly into the ASGI scope: the count that decides is of bytes actually
    received, which is the only number a client cannot lie about.
    """
    app = _app()
    oversize = _payload(TELEMETRY_CAP * 4)
    received: list[int] = []

    async def receive():
        return {"type": "http.request", "body": oversize, "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/client-errors",
        "raw_path": b"/api/client-errors",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [(b"content-length", b"1"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }

    import anyio

    anyio.run(app, scope, receive, send)

    assert sent[0]["status"] == 413, sent
    assert received == []


def test_a_declared_content_length_over_the_cap_is_refused_without_reading(
    client: TestClient,
) -> None:
    """The cheap half. A body that announces itself as oversize costs nothing."""
    app = _app()
    sent: list[dict] = []
    read_calls = 0

    async def receive():
        nonlocal read_calls
        read_calls += 1
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/client-errors",
        "raw_path": b"/api/client-errors",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [
            (b"content-length", str(TELEMETRY_CAP * 10).encode("ascii")),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }

    import anyio

    anyio.run(app, scope, receive, send)

    assert sent[0]["status"] == 413, sent
    assert read_calls == 0, "the body was read despite an oversize Content-Length"


def test_a_body_arriving_in_many_chunks_is_still_counted(client: TestClient) -> None:
    """A client that dribbles the body defeats a check that reads one message."""
    app = _app()
    oversize = _payload(TELEMETRY_CAP * 2)
    chunks = [oversize[i : i + 4096] for i in range(0, len(oversize), 4096)]
    remaining = list(chunks)
    sent: list[dict] = []

    async def receive():
        if remaining:
            body = remaining.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(remaining)}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/client-errors",
        "raw_path": b"/api/client-errors",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }

    import anyio

    anyio.run(app, scope, receive, send)

    assert sent[0]["status"] == 413, sent
    assert remaining, "the whole body was drained before the cap refused it"


def test_the_body_still_reaches_the_handler_intact(client: TestClient) -> None:
    """Buffer-and-replay must not change the bytes, or every bounded route
    silently starts failing validation."""
    app = client.app
    body = json.dumps({"message": "hello éè world"}).encode("utf-8")
    response = client.post(
        "/api/client-errors", content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 200, response.text
    assert app.state.seen == [len("hello éè world")]


def test_the_credential_routes_carry_their_own_tighter_cap(client: TestClient) -> None:
    """An email and a password. The telemetry cap would be four times too loose."""
    assert AUTH_CAP < TELEMETRY_CAP

    ok = client.post(
        "/api/login", content=_payload(AUTH_CAP), headers={"content-type": "application/json"}
    )
    assert ok.status_code == 200, ok.text

    refused = client.post(
        "/api/login",
        content=_payload(AUTH_CAP + 1),
        headers={"content-type": "application/json"},
    )
    assert refused.status_code == 413, refused.text


def test_an_upload_route_is_not_bounded_by_this_middleware(client: TestClient) -> None:
    """The false-positive boundary, and it is load-bearing.

    A cap that applied everywhere would have to exceed `BULK_UPLOAD_MAX_SIZE_MB`
    to let the upload routes work, which would make it no bound at all for a
    telemetry beacon. The upload surface is bounded by `upload_streaming` and
    policed by `test_upload_route_inventory.py`; buffering a 500 MB upload here
    would be the very defect this module exists to prevent.
    """
    app = client.app
    body = b"x" * (TELEMETRY_CAP * 4)
    response = client.post("/api/documents", content=body)
    assert response.status_code == 200, response.text
    assert app.state.seen == [len(body)]


# --------------------------------------------------------------------------- #
# The table, and the rules that decide which cap applies
# --------------------------------------------------------------------------- #


def test_every_unauthenticated_body_route_is_in_the_table() -> None:
    """The denominator. A new public route must not join the API unbounded.

    Derived from the routers rather than listed: `test_upload_route_inventory.py`
    makes the same argument for uploads, and for the same reason - a guard whose
    denominator nobody derives is one route away from being green over a hole.
    """
    import ast
    from pathlib import Path

    routers = Path(__file__).resolve().parents[1] / "routers"
    caps = public_body_caps(settings)
    auth_hints = (
        "current_user",
        "get_current_user",
        "CurrentUser",
        "PolicyService",
        "require_permission",
        "verify_langgraph_token",
    )
    unbounded: list[str] = []

    for path in sorted(routers.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        prefix = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "APIRouter":
                for keyword in node.keywords:
                    if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                        prefix = str(keyword.value.value)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            signature = ast.unparse(node.args)
            if any(hint in signature for hint in auth_hints):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr.lower() not in {"post", "put", "patch"}:
                    continue
                if any(hint in ast.unparse(decorator) for hint in auth_hints):
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                route = "/api" + prefix + str(decorator.args[0].value)
                if cap_for(route, caps) is None:
                    unbounded.append(f"{route} ({path.name}::{node.name})")

    assert not unbounded, (
        "these routes accept a body from an unauthenticated caller and no cap "
        "covers them, so FastAPI materialises whatever is sent before anything "
        "objects:\n  " + "\n  ".join(unbounded)
    )


@pytest.mark.parametrize(
    "path, expected",
    [
        pytest.param("/api/client-errors", TELEMETRY_CAP, id="exact"),
        pytest.param("/api/client-errors/", TELEMETRY_CAP, id="trailing-slash"),
        pytest.param("/api/billing/webhooks/stripe", None, id="webhook-provider"),
        pytest.param("/api/documents", None, id="an-upload-route-is-uncovered"),
        pytest.param("/api/client-errors-other", None, id="a-longer-name-is-not-a-prefix"),
    ],
)
def test_cap_for_matches_on_path_segments_and_not_on_string_prefixes(
    path: str, expected: int | None
) -> None:
    """`/api/client-errors-other` is a different route. A bare `startswith`
    would cap it, and a route silently capped is as wrong as one silently not."""
    caps = public_body_caps(settings)
    result = cap_for(path, caps)
    if expected is None and path.startswith("/api/billing/webhooks"):
        assert result == max(1, int(settings.WEBHOOK_MAX_BODY_SIZE_KB)) * 1024
        return
    assert result == expected


def test_the_caps_are_read_from_configuration() -> None:
    """A literal keeps every test here green while ignoring the deployment."""
    caps = public_body_caps(settings)
    assert caps["/api/client-errors"] == TELEMETRY_CAP
    assert caps["/api/login"] == AUTH_CAP
    assert all(value > 0 for value in caps.values())


def test_the_real_application_installs_the_middleware() -> None:
    """Otherwise every test above measures a miniature nothing shipped."""
    from rbac_backend import main  # noqa: PLC0415

    installed = [entry.cls.__name__ for entry in main.app.user_middleware]
    assert "PublicBodyLimitMiddleware" in installed, installed


# --------------------------------------------------------------------------- #
# The real application, through the real routes
# --------------------------------------------------------------------------- #


def _real_client():
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from rbac_backend.main import app  # noqa: PLC0415

    return TestClient(app, raise_server_exceptions=False)


def test_the_real_client_error_route_refuses_an_oversize_beacon() -> None:
    """The finding, against the deployed route rather than a miniature.

    `POST /api/client-errors` is unauthenticated by design and its docstring
    says it is "IP rate-limited and size-capped so it cannot be abused as a
    write amplifier". The size cap was per field, applied after FastAPI had
    parsed the whole body.
    """
    body = json.dumps({"message": "a" * (200 * 1024)}).encode("utf-8")

    response = _real_client().post(
        "/api/client-errors", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 413, response.text


def test_the_real_client_error_route_still_accepts_a_real_beacon() -> None:
    """The false-positive boundary: telemetry must keep working, or the first
    production incident this surface exists for goes unreported again."""
    body = json.dumps(
        {"message": "ChunkLoadError: Loading chunk 42 failed", "scope": "app-shell"}
    ).encode("utf-8")

    response = _real_client().post(
        "/api/client-errors", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code in (202, 429), response.text


def test_the_real_login_route_refuses_an_oversize_credential_body() -> None:
    """Unauthenticated by necessity: a token cannot be required to get a token."""
    body = json.dumps({"email": "a@b.c", "password": "a" * (200 * 1024)}).encode("utf-8")

    response = _real_client().post(
        "/api/login", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 413, response.text


def test_removing_the_middleware_lets_the_oversize_beacon_through() -> None:
    """The mutation control, against the real route.

    Without this, a 413 arriving from somewhere else - a gateway rule, a
    validation error, a rate limiter - would make every test above pass while
    the middleware did nothing. Rebuilt without it, the same request reaches the
    application and is answered by the model's own validator, which is the
    "decides after the memory has been held" state the fix replaced.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from rbac_backend.main import app  # noqa: PLC0415

    kept = list(app.user_middleware)
    app.user_middleware = [
        entry for entry in kept if entry.cls.__name__ != "PublicBodyLimitMiddleware"
    ]
    app.middleware_stack = app.build_middleware_stack()
    try:
        body = json.dumps({"message": "a" * (200 * 1024)}).encode("utf-8")
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/client-errors",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert response.status_code != 413, (
            "something other than this middleware is producing the 413, so the "
            "tests above prove nothing about it"
        )
    finally:
        app.user_middleware = kept
        app.middleware_stack = app.build_middleware_stack()
