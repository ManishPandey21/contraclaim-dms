import io
import re
import pytest
from fastapi.testclient import TestClient
from rbac_backend.main import app

client = TestClient(app)

@pytest.mark.skip(reason="Integration environment with auth/seed required. Enable when backend is running with valid token and data.")
def test_documents_export_returns_xlsx_with_headers_and_rows():
    # NOTE: This test is marked skipped by default because it requires:
    # - running backend
    # - valid Authorization token
    # - seeded documents
    # To enable, remove the skip marker and set a valid token below.
    token = "<REPLACE_WITH_VALID_BEARER_TOKEN>"

    # No filters => export all documents
    resp = client.get(
        "/api/documents/export",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, f"Status: {resp.status_code}, Body: {resp.text}"

    # Check content type is XLSX
    ctype = resp.headers.get("content-type", "")
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in ctype.lower()

    # Check Content-Disposition filename
    cd = resp.headers.get("content-disposition", "")
    assert re.search(r'filename\s*=\s*"?documents_export\.xlsx"?', cd, re.IGNORECASE)

    # Validate basic XLSX structure by reading the first few bytes (PK zip header)
    content = resp.content
    assert isinstance(content, (bytes, bytearray))
    assert content[:2] == b"PK", "XLSX files are ZIP-based and should start with PK"

    # Optional: parse with openpyxl to validate headers
    try:
        from openpyxl import load_workbook
    except Exception:
        pytest.skip("openpyxl not available in test environment")

    wb = load_workbook(io.BytesIO(content))
    ws = wb.active
    headers = [ws.cell(row=1, column=i).value for i in range(1, 11)]
    expected_headers = [
        "Date", "Letter No.", "Direction", "From", "To", "Subject",
        "Tag", "Sub-Tag", "Status", "Project", "Upload Date"
    ]
    assert headers == expected_headers

    # At least one data row exists
    assert ws.max_row >= 2
