"""M-01 part (plan 4B-41, D-21): indexes for skill evaluation runs.

``skill_eval_runs`` holds one immutable record per evaluation run made on the
server. A run is read by its id (the ``_id``, unique by default) and listed per
skill version, newest first, on the publish panel and when an approval or a
publication names a run. This adds that listing index, and one on the run an
approval or a version names, so the approval history can be traced to its run.

Index only. No run, approval or version is read, changed or backfilled.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult
from .v20261114_0001_skill_registry import apply_indexes

VERSION = "20261123_0001"
NAME = "skill_eval_runs_indexes"
DESCRIPTION = "Index skill evaluation runs by version and time, and approvals by the run they name. Nothing is changed or backfilled."

INDEXES: List[Dict[str, Any]] = [
    {
        "collection": "skill_eval_runs",
        "keys": [("skill_key", 1), ("version", 1), ("started_at", -1)],
        "name": "skill_eval_run_version_time",
    },
    {
        "collection": "skill_approvals",
        "keys": [("eval_run_id", 1)],
        "name": "skill_approval_eval_run",
        "partialFilterExpression": {"eval_run_id": {"$type": "string"}},
    },
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=await apply_indexes(db, INDEXES, dry_run),
        notices=[
            "Index only. No evaluation run, approval or skill version is changed; nothing is backfilled.",
        ],
    )
