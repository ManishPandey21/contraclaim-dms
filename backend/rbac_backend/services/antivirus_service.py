"""Asynchronous ClamAV client service using standard clamd TCP streaming."""

from __future__ import annotations

import asyncio
import logging
import struct
from pathlib import Path
from typing import Optional

from ..core.config import settings

logger = logging.getLogger(__name__)


class AntivirusService:
    """Central service for scanning uploaded files via ClamAV clamd daemon."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        timeout: Optional[int] = None,
        fail_open: Optional[bool] = None,
    ) -> None:
        self.host = host or settings.CLAMAV_HOST
        self.port = port or settings.CLAMAV_PORT
        self.timeout = timeout or settings.CLAMAV_TIMEOUT
        self.fail_open = fail_open if fail_open is not None else settings.CLAMAV_FAIL_OPEN

    async def scan_file(self, file_path: Path) -> tuple[bool, Optional[str]]:
        """Scan a local file by streaming its chunks over network socket to ClamAV.

        Returns:
            (is_clean, detail_or_virus_name)
            - True, None: File is clean
            - False, virus_name: Infected file
            - True/False, error_msg: Connection or scan failures depending on fail-open rules
        """
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return False, f"Target file does not exist: {path}"

        logger.debug("Initiating antivirus scan for %s", path.name)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=5.0
            )
        except Exception as exc:
            logger.error("Failed to connect to ClamAV daemon at %s:%d: %s", self.host, self.port, exc)
            if self.fail_open:
                logger.warning("Antivirus offline; bypassing (CLAMAV_FAIL_OPEN=true)")
                return True, f"Bypassed (Antivirus offline: {exc})"
            return False, f"Antivirus offline: {exc}"

        try:
            # Send NULL-terminated clamd INSTREAM command
            writer.write(b"zINSTREAM\0")
            await writer.drain()

            # Read and stream file blocks
            chunk_size = 512 * 1024  # 512 KB chunks
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    # Pack chunk length as a 4-byte big-endian unsigned integer
                    length_header = struct.pack(">I", len(chunk))
                    writer.write(length_header + chunk)
                    await writer.drain()

            # Write end-of-stream delimiter (4 bytes of 0)
            writer.write(struct.pack(">I", 0))
            await writer.drain()

            # Wait for ClamAV scan response
            raw_response = await asyncio.wait_for(
                reader.read(1024),
                timeout=float(self.timeout)
            )
            response = raw_response.decode("utf-8", errors="ignore").strip()
            logger.debug("ClamAV raw response: %s", response)

            # Response Parsing logic
            # Clean: "stream: OK"
            # Infected: "stream: Eicar-Signature FOUND"
            # Error: "stream: ExceededSizeLimit ERROR"
            if "FOUND" in response:
                virus_name = response.split("FOUND")[0].replace("stream:", "").strip()
                logger.critical("Infected file detected! File: %s, Virus: %s", path.name, virus_name)
                return False, virus_name
            
            if "OK" in response:
                logger.debug("File %s is CLEAN", path.name)
                return True, None

            if "ERROR" in response:
                error_detail = response.replace("stream:", "").strip()
                logger.error("ClamAV scan error: %s", error_detail)
                if self.fail_open:
                    return True, f"Bypassed (Scan error: {error_detail})"
                return False, f"ClamAV scan error: {error_detail}"

            # Fallback for unexpected payloads
            logger.error("Unexpected ClamAV daemon payload: %s", response)
            if self.fail_open:
                return True, f"Bypassed (Unexpected scan payload: {response})"
            return False, f"Unexpected scan payload: {response}"

        except Exception as exc:
            import traceback
            traceback.print_exc()
            logger.error("Antivirus scanning execution failed: %s", exc)
            if self.fail_open:
                return True, f"Bypassed (Scan exception: {exc})"
            return False, f"Scan exception: {exc}"
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
