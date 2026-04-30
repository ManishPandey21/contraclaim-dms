from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from rbac_backend.core.errors import register_exception_handlers
from rbac_backend.utils.error_handler import (
    DocumentError,
    handle_exceptions,
    raise_not_found,
)


app = FastAPI()
register_exception_handlers(app)


@app.get("/http-exception")
async def http_exception():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="missing")


@app.get("/domain-exception")
@handle_exceptions
def domain_exception():
    raise DocumentError("document missing", http_status=status.HTTP_404_NOT_FOUND)


@app.get("/not-found")
def not_found():
    raise_not_found("Widget", "abc")


@app.get("/unexpected")
@handle_exceptions
def unexpected_error():
    raise RuntimeError("boom")


@app.post("/requires-body")
async def requires_body(payload: dict):  # pragma: no cover - executed via client
    return payload


def create_client() -> TestClient:
    return TestClient(app)


def test_http_exception_is_normalised():
    client = create_client()
    response = client.get("/http-exception")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "NotFoundError"
    assert payload["message"] == "missing"


def test_domain_exception_uses_error_response():
    client = create_client()
    response = client.get("/domain-exception")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "DocumentError"
    assert payload["message"] == "document missing"


def test_raise_not_found_generates_structured_payload():
    client = create_client()
    response = client.get("/not-found")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "NotFoundError"
    assert payload["details"]["resource"] == "Widget"
    assert payload["details"]["id"] == "abc"


def test_unexpected_error_is_wrapped():
    client = create_client()
    response = client.get("/unexpected")
    assert response.status_code == 500
    payload = response.json()
    assert payload["error"] == "InternalServerError"
    assert payload["message"] == "Internal server error"


def test_request_validation_error_shape():
    client = create_client()
    response = client.post("/requires-body", json=None)
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "RequestValidationError"
    assert "errors" in payload["details"]
