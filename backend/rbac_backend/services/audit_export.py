"""Audit event export helpers (Phase 3 / M7).

Turns audit-event records into an arbitration-friendly CSV evidence pack. Pure
functions so they are trivially testable without a database.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List

# Stable, human-meaningful column order for the evidence pack.
AUDIT_CSV_COLUMNS = [
    "created_at",
    "action",
    "result",
    "reason",
    "actor_id",
    "organization_id",
    "project_id",
    "resource_type",
    "resource_id",
    "correlation_id",
    "metadata",
]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, default=str, sort_keys=True)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def audit_events_to_csv(events: List[Dict[str, Any]]) -> str:
    """Render audit events as a CSV string with a fixed, stable column set."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(AUDIT_CSV_COLUMNS)
    for event in events or []:
        writer.writerow([_cell(event.get(col)) for col in AUDIT_CSV_COLUMNS])
    return buffer.getvalue()
