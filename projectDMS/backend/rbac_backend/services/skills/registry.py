"""Skill registry: draft, publish, deprecate, bind and resolve skill versions.

Invariants enforced here (not only in the router):

- A published or deprecated version is write-once. Its text, references,
  manifest and hash never change; a correction is a new version.
- Publishing requires a passing evaluation report and a clean guardrail scan,
  and, where the manifest names them, technical and legal approvals bound to
  this exact text and evaluation, given by people other than the publisher.
- Resolution is deterministic: project binding, then organisation binding,
  then the latest published version. A disabled binding hides the skill.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from ...models.skill import (
    SYSTEM_SCOPE,
    EffectiveSkill,
    Skill,
    SkillAdminListResponse,
    SkillBinding,
    SkillApproval,
    SkillBindingRequest,
    SkillEvalReport,
    SkillKind,
    SkillManifest,
    SkillReference,
    SkillVersion,
    SkillVersionSummary,
)
from ...utils.datetime import now_utc
from .approvals import (
    SkillApprovalService,
    approval_statuses,
    describe_unsatisfied,
    require_acceptable_report,
    required_reviews,
    revision_filter,
)
from .transactions import audit_skill_decision, run_in_transaction, session_kwargs
from .loader import LoadedSkill
from .system_skills import (
    AWAITING_APPROVAL_REASON,
    KNOWN_UNINSTALLED_SKILLS,
    system_skill_versions,
    with_approval_floor,
)

logger = logging.getLogger(__name__)

SKILLS = "skills"
SKILL_VERSIONS = "skill_versions"
SKILL_BINDINGS = "skill_bindings"


def semver_tuple(version: str) -> Tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def _strip_id(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


class SkillRegistry:
    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------ reads
    async def _find(self, collection: str, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self.db is None:
            return []
        try:
            return await self.db[collection].find(query).to_list(length=1000)
        except Exception as exc:  # fail visible in logs, degrade to bundled skills
            logger.warning("skill registry read failed on %s: %s", collection, exc)
            return []

    async def get_version(self, skill_key: str, version: str) -> Optional[SkillVersion]:
        if self.db is not None:
            doc = await self.db[SKILL_VERSIONS].find_one(
                {"skill_key": skill_key, "version": version}
            )
            if doc:
                return SkillVersion(**doc)
        for bundled in system_skill_versions():
            if bundled.skill_key == skill_key and bundled.version == version:
                return bundled
        return None

    async def _all_versions(self) -> List[SkillVersion]:
        """DB versions, plus bundled system versions the DB does not hold yet."""
        versions = [SkillVersion(**doc) for doc in await self._find(SKILL_VERSIONS, {})]
        seen = {(item.skill_key, item.version) for item in versions}
        for bundled in system_skill_versions():
            if (bundled.skill_key, bundled.version) not in seen:
                versions.append(bundled)
        return versions

    async def _binding(
        self, skill_key: str, organization_id: Optional[str], project_id: Optional[str]
    ) -> Optional[SkillBinding]:
        if self.db is None or not organization_id:
            return None
        for scope_project in ([project_id, None] if project_id else [None]):
            doc = await self.db[SKILL_BINDINGS].find_one(
                {
                    "organization_id": str(organization_id),
                    "project_id": str(scope_project) if scope_project else None,
                    "skill_key": skill_key,
                }
            )
            if doc:
                return SkillBinding(**doc)
        return None

    async def resolve(
        self,
        skill_key: str,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Tuple[Optional[SkillVersion], Optional[SkillBinding], Optional[str]]:
        """Return (version, binding, reason-if-unavailable) for new use."""
        binding = await self._binding(skill_key, organization_id, project_id)
        if binding and not binding.enabled:
            return None, binding, "Turned off for this project"
        candidates = [
            item
            for item in await self._all_versions()
            if item.skill_key == skill_key
            and item.owner_scope in {SYSTEM_SCOPE, str(organization_id or "")}
        ]
        if not candidates:
            return None, binding, "Not installed"
        if binding and binding.pinned_version:
            pinned = next(
                (item for item in candidates if item.version == binding.pinned_version), None
            )
            if pinned is None:
                return None, binding, f"Pinned version {binding.pinned_version} was not found"
            if pinned.status != "published":
                return None, binding, f"Pinned version {pinned.version} is {pinned.status}"
            return pinned, binding, None
        published = [item for item in candidates if item.status == "published"]
        if not published:
            return None, binding, "No published version"
        published.sort(
            key=lambda item: (item.owner_scope != SYSTEM_SCOPE, semver_tuple(item.version))
        )
        return published[-1], binding, None

    async def list_effective(
        self, organization_id: Optional[str] = None, project_id: Optional[str] = None
    ) -> List[EffectiveSkill]:
        keys = sorted({item.skill_key for item in await self._all_versions()})
        result: List[EffectiveSkill] = []
        for key in keys:
            version, binding, reason = await self.resolve(key, organization_id, project_id)
            if version is None:
                any_version = next(
                    (item for item in await self._all_versions() if item.skill_key == key), None
                )
                if any_version is None or any_version.status == "draft" and reason == "No published version":
                    continue  # drafts are invisible to drafters
                result.append(
                    EffectiveSkill(
                        skill_key=key,
                        name=any_version.name,
                        description=any_version.description,
                        kind=any_version.kind,
                        enabled=bool(binding.enabled) if binding else True,
                        available=False,
                        unavailable_reason=reason,
                    )
                )
                continue
            result.append(self.effective(version, binding))
        installed = {item.skill_key for item in result}
        for key, label in KNOWN_UNINSTALLED_SKILLS.items():
            if key not in installed:
                # A hand-off target with only unpublished versions is installed
                # but unreviewed: say so, rather than "Not installed".
                result.append(
                    EffectiveSkill(
                        skill_key=key,
                        name=label,
                        kind="evaluation",
                        available=False,
                        unavailable_reason=AWAITING_APPROVAL_REASON if key in keys else "Not installed",
                    )
                )
        return result

    @staticmethod
    def effective(version: SkillVersion, binding: Optional[SkillBinding] = None) -> EffectiveSkill:
        manifest = version.manifest
        return EffectiveSkill(
            skill_key=version.skill_key,
            name=version.name,
            description=version.description,
            kind=version.kind,
            use_when=manifest.use_when,
            version=version.version,
            content_hash=version.content_hash,
            roles_supported=list(manifest.roles_supported),
            native_role=manifest.native_role,
            contract_forms=list(manifest.contract_forms),
            output_schema_key=manifest.output_schema_key,
            chains_to=list(manifest.chains_to),
            required_inputs=list(manifest.required_inputs),
            enabled=True,
            pinned=bool(binding and binding.pinned_version),
            available=True,
        )

    async def draft_versions(self) -> List[SkillVersion]:
        """Every draft, for the reviewers' queue."""
        return [item for item in await self._all_versions() if item.status == "draft"]

    async def admin_list(self, organization_id: Optional[str] = None) -> SkillAdminListResponse:
        versions = await self._all_versions()
        skills: Dict[str, Skill] = {}
        for item in sorted(versions, key=lambda v: (v.skill_key, semver_tuple(v.version))):
            skill = skills.setdefault(
                item.skill_key,
                Skill(
                    skill_key=item.skill_key,
                    owner_scope=item.owner_scope,
                    name=item.name,
                    description=item.description,
                    kind=item.kind,
                ),
            )
            if item.status == "published":
                skill.latest_published_version = item.version
        query = {"organization_id": str(organization_id)} if organization_id else {}
        bindings = [SkillBinding(**doc) for doc in await self._find(SKILL_BINDINGS, query)]
        return SkillAdminListResponse(
            skills=list(skills.values()),
            versions=[
                SkillVersionSummary(
                    skill_key=item.skill_key,
                    version=item.version,
                    name=item.name,
                    kind=item.kind,
                    status=item.status,
                    content_hash=item.content_hash,
                    owner_scope=item.owner_scope,
                    eval_report=item.eval_report,
                    created_at=item.created_at,
                    published_at=item.published_at,
                    required_approvals=required_reviews(item),
                )
                for item in versions
            ],
            bindings=bindings,
        )

    # ----------------------------------------------------------------- writes
    async def create_draft(
        self,
        loaded: LoadedSkill,
        *,
        version: str,
        kind: SkillKind,
        manifest: SkillManifest,
        created_by: Optional[str],
        guardrail_findings: Optional[List[Dict[str, Any]]] = None,
        bundle_file_id: Optional[str] = None,
    ) -> SkillVersion:
        existing = await self.get_version(loaded.skill_key, version)
        if existing and existing.status != "draft":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{loaded.skill_key} {version} is {existing.status} and cannot be changed. "
                    "Import the correction as a new version."
                ),
            )
        if existing and await SkillApprovalService(self.db).live_approvals(loaded.skill_key, version):
            # Replacing reviewed text or policy under the same version number
            # would leave reviewers' names on something they did not see.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{loaded.skill_key} {version} has approvals. Import the change as a new version, "
                    "or have the approvals revoked first."
                ),
            )
        # The server floor (plan D-05): a manifest may add reviews, never drop one.
        manifest = manifest.model_copy(
            update={"required_approvals": with_approval_floor(loaded.skill_key, list(manifest.required_approvals))}
        )
        record = SkillVersion(
            skill_key=loaded.skill_key,
            version=version,
            owner_scope=SYSTEM_SCOPE,
            name=loaded.name,
            description=loaded.description,
            kind=kind,
            content_hash=loaded.content_hash,
            skill_md=loaded.skill_md,
            references=[
                SkillReference(path=ref.path, text=ref.text, content_hash=ref.content_hash)
                for ref in loaded.references
            ],
            manifest=manifest,
            status="draft",
            bundle_file_id=bundle_file_id,
            guardrail_findings=list(guardrail_findings or []),
            created_by=created_by,
        )
        payload = _strip_id(record.model_dump(by_alias=True))
        # The status filter makes the replace a no-op if a concurrent publish won.
        result = await self.db[SKILL_VERSIONS].update_one(
            {"skill_key": record.skill_key, "version": record.version, "status": "draft"},
            {"$set": payload},
        )
        if not getattr(result, "matched_count", 0):
            await self.db[SKILL_VERSIONS].insert_one(payload)
        return record

    async def publish(
        self,
        skill_key: str,
        version: str,
        eval_report: SkillEvalReport,
        published_by: Optional[str],
        eval_run_id: Optional[str] = None,
    ) -> SkillVersion:
        """Publish a draft, in one transaction with the approval check (plan D-05a).

        The version and its approvals are read inside the transaction, and the
        version is written only if ``approval_revision`` is still the value read.
        A concurrent record or revoke writes the same document, so one of the two
        is aborted and retried: a publish can never commit on approvals that
        were revoked or added after it looked.
        """
        require_acceptable_report(eval_report)
        publisher = str(published_by or "")
        if not publisher:
            raise HTTPException(status_code=403, detail="Publishing needs an identified publisher")
        if publisher == str(eval_report.recorded_by):
            raise HTTPException(
                status_code=409,
                detail="The person who ran the evaluation cannot publish the version",
            )
        approvals_service = SkillApprovalService(self.db)

        async def decide(session: Optional[Any]) -> SkillVersion:
            kw = session_kwargs(session)
            doc = await self.db[SKILL_VERSIONS].find_one({"skill_key": skill_key, "version": version}, **kw)
            if not doc:
                raise HTTPException(status_code=404, detail="Skill version not found")
            record = SkillVersion(**doc)
            if record.status != "draft":
                raise HTTPException(
                    status_code=409, detail=f"{skill_key} {version} is already {record.status}"
                )
            if any(
                str(finding.get("severity")) == "critical" for finding in record.guardrail_findings
            ):
                raise HTTPException(
                    status_code=422,
                    detail="This version contains text flagged as instruction manipulation and cannot be published.",
                )
            if not eval_report.passed:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Evaluation gate not met: score {eval_report.score:.2f} is below the "
                        f"threshold {eval_report.threshold:.2f}, or no cases were run."
                    ),
                )
            approvals = await approvals_service._list_in(skill_key, version, kw)
            _require_approvals(record, eval_report, publisher, approvals)
            result = await self.db[SKILL_VERSIONS].update_one(
                {
                    "skill_key": skill_key,
                    "version": version,
                    "status": "draft",
                    "content_hash": record.content_hash,
                    **revision_filter(record.approval_revision),
                },
                {
                    "$set": {
                        "status": "published",
                        "eval_report": eval_report.model_dump(),
                        "eval_run_id": eval_run_id,
                        "published_by": publisher,
                        "published_at": now_utc(),
                    },
                    "$inc": {"approval_revision": 1},
                },
                **kw,
            )
            if not getattr(result, "matched_count", 0):
                raise HTTPException(status_code=409, detail="Version changed while publishing; retry")
            await audit_skill_decision(
                self.db,
                session,
                action="skill.version.publish",
                actor_id=publisher,
                skill_key=skill_key,
                version=version,
                before={"status": "draft"},
                after={"status": "published"},
                metadata={
                    "content_hash": record.content_hash,
                    "eval_run_id": eval_run_id,
                    "eval_score": eval_report.score,
                    "eval_threshold": eval_report.threshold,
                    "approval_ids": sorted(item.id or "" for item in approvals if item.revoked_at is None),
                },
            )
            return record

        record = await run_in_transaction(self.db, decide)
        await self._refresh_skill(record)
        return await self._require_db_version(skill_key, version)

    async def deprecate(
        self, skill_key: str, version: str, deprecated_by: Optional[str]
    ) -> SkillVersion:
        """Deprecate a published version: one transaction for the read, any materialisation,
        the status change and the audit event (4B-05).

        A version that exists only in the bundle is first written to the database so the
        deprecation is durable; that write is inside the same transaction, so a failure or
        a refusal leaves no materialised copy behind. The copy's deterministic ``_id`` makes
        two concurrent materialisations conflict (with or without the migration's
        ``skill_version_unique`` index), and the retried one then reads the other's result. Without transactions (a standalone server) the
        call is refused with 503 before anything is written.
        """

        async def decide(session: Optional[Any]) -> SkillVersion:
            kw = session_kwargs(session)
            doc = await self.db[SKILL_VERSIONS].find_one({"skill_key": skill_key, "version": version}, **kw)
            if doc:
                record = SkillVersion(**doc)
            else:
                bundled_match: Optional[SkillVersion] = next(
                    (
                        bundled
                        for bundled in system_skill_versions()
                        if bundled.skill_key == skill_key and bundled.version == version
                    ),
                    None,
                )
                if bundled_match is None:
                    raise HTTPException(status_code=404, detail="Skill version not found")
                record = bundled_match
            if record.status != "published":
                raise HTTPException(
                    status_code=409, detail=f"Only a published version can be deprecated ({record.status})"
                )
            materialised = doc is None
            if materialised:
                # Bundled-only version: materialise it so the deprecation is durable. A
                # deterministic _id makes two concurrent materialisations collide on the
                # _id index, which every collection has, so this holds even where the
                # migration's skill_version_unique index was never created (review finding 5).
                materialised_doc = _strip_id(record.model_dump(by_alias=True))
                materialised_doc["_id"] = f"bundled:{skill_key}@{version}"
                await self.db[SKILL_VERSIONS].insert_one(materialised_doc, **kw)
            result = await self.db[SKILL_VERSIONS].update_one(
                {"skill_key": skill_key, "version": version, "status": "published"},
                {
                    "$set": {
                        "status": "deprecated",
                        "deprecated_by": deprecated_by,
                        "deprecated_at": now_utc(),
                    },
                    "$inc": {"approval_revision": 1},
                },
                **kw,
            )
            if not getattr(result, "matched_count", 0):
                raise HTTPException(status_code=409, detail="The version changed at the same time; reload")
            await audit_skill_decision(
                self.db,
                session,
                action="skill.version.deprecate",
                actor_id=deprecated_by,
                skill_key=skill_key,
                version=version,
                before={"status": "published"},
                after={"status": "deprecated"},
                metadata={"cause": "direct", "materialised": materialised},
            )
            return record

        record = await run_in_transaction(self.db, decide)
        await self._refresh_skill(record)
        return await self._require_db_version(skill_key, version)

    async def set_binding(self, request: SkillBindingRequest, updated_by: Optional[str]) -> SkillBinding:
        if request.pinned_version:
            pinned = await self.get_version(request.skill_key, request.pinned_version)
            if pinned is None or pinned.status != "published":
                raise HTTPException(
                    status_code=422,
                    detail="Only a published version can be pinned",
                )
        elif not any(
            item.skill_key == request.skill_key for item in await self._all_versions()
        ):
            raise HTTPException(status_code=404, detail="Skill not found")
        binding = SkillBinding(
            organization_id=str(request.organization_id),
            project_id=str(request.project_id) if request.project_id else None,
            skill_key=request.skill_key,
            pinned_version=request.pinned_version,
            enabled=request.enabled,
            role_overlay_key=request.role_overlay_key,
            updated_by=updated_by,
        )
        await self.db[SKILL_BINDINGS].update_one(
            {
                "organization_id": binding.organization_id,
                "project_id": binding.project_id,
                "skill_key": binding.skill_key,
            },
            {"$set": binding.model_dump()},
            upsert=True,
        )
        return binding

    # ---------------------------------------------------------------- helpers
    async def _require_db_version(self, skill_key: str, version: str) -> SkillVersion:
        doc = await self.db[SKILL_VERSIONS].find_one({"skill_key": skill_key, "version": version})
        if not doc:
            raise HTTPException(status_code=404, detail="Skill version not found")
        return SkillVersion(**doc)

    async def refresh_published_pointer(self, skill_key: str, version: str) -> None:
        """Recompute the skill's latest-published pointer after a status change made elsewhere."""
        record = await self.get_version(skill_key, version)
        if record is not None:
            await self._refresh_skill(record)

    async def _refresh_skill(self, record: SkillVersion) -> None:
        published = [
            item.version
            for item in await self._all_versions()
            if item.skill_key == record.skill_key and item.status == "published"
        ]
        latest = max(published, key=semver_tuple) if published else None
        await self.db[SKILLS].update_one(
            {"owner_scope": record.owner_scope, "skill_key": record.skill_key},
            {
                "$set": {
                    "owner_scope": record.owner_scope,
                    "skill_key": record.skill_key,
                    "name": record.name,
                    "description": record.description,
                    "kind": record.kind,
                    "status": "active",
                    "latest_published_version": latest,
                    "updated_at": now_utc(),
                }
            },
            upsert=True,
        )


def _require_approvals(
    record: SkillVersion, eval_report: SkillEvalReport, publisher: str, approvals: List[SkillApproval]
) -> None:
    """Every required review satisfied, by different people, none of them the publisher's legal sign-off."""
    statuses = approval_statuses(record, eval_report, approvals)
    unsatisfied = describe_unsatisfied(statuses)
    if unsatisfied:
        raise HTTPException(
            status_code=422,
            detail="Approval gate not met: " + "; ".join(unsatisfied),
        )
    reviewers = [item.approval.reviewer_user_id for item in statuses if item.approval is not None]
    if len(set(reviewers)) != len(reviewers):
        # Plan D-07: two required reviews from one person are one review.
        raise HTTPException(
            status_code=409,
            detail="Each required review must come from a different person",
        )
    if str(eval_report.recorded_by) in reviewers:
        raise HTTPException(status_code=409, detail="The person who ran the evaluation cannot also approve it")
    if any(
        item.kind == "legal" and item.approval and item.approval.reviewer_user_id == publisher
        for item in statuses
    ):
        raise HTTPException(
            status_code=409,
            detail="The legal signatory cannot publish the version they approved",
        )


__all__ = ["SKILLS", "SKILL_BINDINGS", "SKILL_VERSIONS", "SkillRegistry", "semver_tuple"]
