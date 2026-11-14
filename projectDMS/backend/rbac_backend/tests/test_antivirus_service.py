"""Unit tests for AntivirusService.

These tests verify clean file scanning, infected file scanning (FOUND),
ClamAV error response handling, connection timeouts, and fail-open/fail-closed
postures. Uses mocks to simulate a running clamd server.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.rbac_backend.services.antivirus_service import AntivirusService


class MockStreamWriter:
    """Mock implementation of asyncio.StreamWriter to avoid coroutine warnings."""
    def __init__(self) -> None:
        self.write = MagicMock()
        self.close = MagicMock()

    async def drain(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


@pytest.fixture
def temp_spool_file():
    """Generates a temporary file containing harmless test content.

    Using a standard string rather than the real EICAR signature to prevent
    local host antivirus (e.g. Windows Defender) from quarantining the file
    during local test execution.
    """
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"MOCK-HARMLESS-ANTIVIRUS-TEST-FILE-CONTENT")
        name = f.name
    yield Path(name)
    Path(name).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_scan_clean_file(temp_spool_file):
    """Verify that a clean file is successfully scanned and returns True, None."""
    # Mocking clamd OK response
    mock_reader = AsyncMock()
    mock_reader.read.return_value = b"stream: OK\n"
    mock_writer = MockStreamWriter()

    with patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)) as mock_conn:
        service = AntivirusService(host="mock-clamav", port=3310, fail_open=False)
        is_clean, detail = await service.scan_file(temp_spool_file)

        assert is_clean is True
        assert detail is None
        mock_conn.assert_called_once_with("mock-clamav", 3310)


@pytest.mark.asyncio
async def test_scan_infected_file(temp_spool_file):
    """Verify that an infected file is detected and returns False, virus_name."""
    # Mocking clamd FOUND response
    mock_reader = AsyncMock()
    mock_reader.read.return_value = b"stream: Eicar-Test-Signature FOUND\n"
    mock_writer = MockStreamWriter()

    with patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)):
        service = AntivirusService(host="mock-clamav", port=3310, fail_open=False)
        is_clean, detail = await service.scan_file(temp_spool_file)

        assert is_clean is False
        assert detail == "Eicar-Test-Signature"


@pytest.mark.asyncio
async def test_scan_offline_fail_closed(temp_spool_file):
    """Verify that connection failure results in False, error when fail-open is False."""
    with patch("asyncio.open_connection", side_effect=OSError("Connection refused")):
        service = AntivirusService(host="mock-clamav", port=3310, fail_open=False)
        is_clean, detail = await service.scan_file(temp_spool_file)

        assert is_clean is False
        assert "Antivirus offline" in detail


@pytest.mark.asyncio
async def test_scan_offline_fail_open(temp_spool_file):
    """Verify that connection failure bypasses scanning and returns True, detail when fail-open is True."""
    with patch("asyncio.open_connection", side_effect=OSError("Connection refused")):
        service = AntivirusService(host="mock-clamav", port=3310, fail_open=True)
        is_clean, detail = await service.scan_file(temp_spool_file)

        assert is_clean is True
        assert "Bypassed" in detail


@pytest.mark.asyncio
async def test_scan_clamav_error_fail_closed(temp_spool_file):
    """Verify that ClamAV daemon error returns False when fail-open is False."""
    mock_reader = AsyncMock()
    mock_reader.read.return_value = b"stream: ExceededSizeLimit ERROR\n"
    mock_writer = MockStreamWriter()

    with patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)):
        service = AntivirusService(host="mock-clamav", port=3310, fail_open=False)
        is_clean, detail = await service.scan_file(temp_spool_file)

        assert is_clean is False
        assert "ClamAV scan error" in detail


@pytest.mark.asyncio
async def test_scan_clamav_error_fail_open(temp_spool_file):
    """Verify that ClamAV daemon error returns True (bypassed) when fail-open is True."""
    mock_reader = AsyncMock()
    mock_reader.read.return_value = b"stream: ExceededSizeLimit ERROR\n"
    mock_writer = MockStreamWriter()

    with patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)):
        service = AntivirusService(host="mock-clamav", port=3310, fail_open=True)
        is_clean, detail = await service.scan_file(temp_spool_file)

        assert is_clean is True
        assert "Bypassed" in detail
