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

from typing import Any, Dict, Mapping, Optional, Set

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


def pipeline_version_of(job: Mapping[str, Any]) -> str:
    """The pipeline a claimed job must be processed by.

    Absent or unrecognised values resolve to legacy: a job queued before this
    existed predates the unified path, and an unknown value is not a licence to
    run the newer code.
    """
    version = (job or {}).get("pipeline_version")
    return UNIFIED_PIPELINE if version == UNIFIED_PIPELINE else LEGACY_PIPELINE


def claim_filter_for(pipeline_versions: Optional[Set[str]]) -> Dict[str, Any]:
    """Mongo filter restricting a worker to the versions it may process.

    An empty or absent restriction means "claim anything", which is the
    single-worker default. Restricting a worker is what makes a canary real: a
    unified worker physically cannot claim another tenant's legacy job.

    A legacy-capable worker also matches jobs with no recorded version, because
    everything queued before this change is legacy by definition.
    """
    if not pipeline_versions:
        return {}

    versions = sorted(pipeline_versions)
    matches: Dict[str, Any] = {"pipeline_version": {"$in": versions}}
    if LEGACY_PIPELINE not in pipeline_versions:
        return matches

    return {
        "$or": [
            matches,
            {"pipeline_version": {"$exists": False}},
            {"pipeline_version": None},
        ]
    }
