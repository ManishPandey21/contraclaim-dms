"""Which extraction pipeline a document job belongs to.

The decision is made once, at job creation, and persisted on the job. Readers
honour the persisted value rather than re-deciding from current configuration.

That ordering is the whole safety property. If a worker re-derived the answer
at claim time, emptying the canary allowlist would silently reclassify work
already in flight, and a canary worker could sweep up every tenant's jobs. With
the version pinned on the job, the allowlist only ever affects *new* work.

Everything fails closed onto the legacy path: an unknown scope, a missing
organisation, or a job queued before this existed all stay legacy.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Set

LEGACY_PIPELINE = "legacy_v0"
UNIFIED_PIPELINE = "unified_v1"


def resolve_pipeline_version(
    *,
    organization_id: Optional[str],
    enabled: bool,
    canary_org_ids: Set[str],
) -> str:
    """Decide the pipeline for a job being created."""
    if enabled:
        return UNIFIED_PIPELINE
    if not organization_id:
        return LEGACY_PIPELINE
    return (
        UNIFIED_PIPELINE if str(organization_id) in canary_org_ids else LEGACY_PIPELINE
    )


def uses_unified_pipeline(job: Mapping[str, Any]) -> bool:
    """Read a job's pipeline from what was persisted, not from config."""
    return (job or {}).get("pipeline_version") == UNIFIED_PIPELINE
