from fastapi.testclient import TestClient

from rbac_backend.main import app


def test_document_delete_preflight_allows_step_up_header():
    client = TestClient(app)

    response = client.options(
        "/api/documents/000000000000000000000000",
        headers={
            "Origin": "https://web.contraclaim.com",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "x-step-up-token,x-csrf-token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://web.contraclaim.com"
    allow_headers = response.headers["access-control-allow-headers"].lower()
    assert "x-step-up-token" in allow_headers
    assert "x-csrf-token" in allow_headers
