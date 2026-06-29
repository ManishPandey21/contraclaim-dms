"""Minimal post-deploy smoke checks for production/staging releases."""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.getenv("SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT_SECONDS = float(os.getenv("SMOKE_TIMEOUT_SECONDS", "5"))
ATTEMPTS = int(os.getenv("SMOKE_ATTEMPTS", "12"))
SLEEP_SECONDS = float(os.getenv("SMOKE_SLEEP_SECONDS", "5"))
CHECK_OPERATIONS = os.getenv("SMOKE_CHECK_OPERATIONS", "false").lower() == "true"
METRICS_TOKEN = os.getenv("METRICS_TOKEN", "").strip()


def _get_json(path: str) -> dict:
    headers = {"Accept": "application/json"}
    if METRICS_TOKEN and path in {"/health/operations", "/health/observability"}:
        headers["X-Metrics-Token"] = METRICS_TOKEN
    request = Request(f"{BASE_URL}{path}", headers=headers)
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        data = response.read().decode("utf-8")
        return json.loads(data) if data else {}


def main() -> int:
    checks = ["/health/live", "/health/ready"]
    if CHECK_OPERATIONS:
        checks.append("/health/operations")
    last_error = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            results = {path: _get_json(path) for path in checks}
            print(json.dumps({"status": "ok", "base_url": BASE_URL, "results": results}, indent=2))
            return 0
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            print(f"Smoke attempt {attempt}/{ATTEMPTS} failed: {last_error}", file=sys.stderr)
            if attempt < ATTEMPTS:
                time.sleep(SLEEP_SECONDS)

    print(
        json.dumps(
            {
                "status": "failed",
                "base_url": BASE_URL,
                "last_error": last_error,
            },
            indent=2,
        ),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
