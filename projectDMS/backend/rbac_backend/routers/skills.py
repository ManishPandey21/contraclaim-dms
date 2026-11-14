"""Skill registry API.

Reading the skills in force is open to drafters in scope. Importing,
publishing, deprecating and pinning change legal output and are restricted:
publish and deprecate are platform-level (system skills only in this release),
pinning is per organisation or project.
"""

from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError as PydanticValidationError

from ..core.config import settings
from ..core.database import get_database
from ..core.security import CurrentUser, get_current_user
from ..models.skill import (
    EffectiveSkill,
    SkillAdminListResponse,
    SkillBinding,
    SkillBindingRequest,
    SkillImportOptions,
    SkillManifest,
    SkillPublishRequest,
    SkillVersion,
)
from ..services.ai_guardrails import AIOutputGuardrailService
from ..services.policy_service import PolicyService
from ..services.skills.loader import MAX_BUNDLE_BYTES, SkillBundleError, load_skill_zip
from ..services.skills.registry import SkillRegistry
from ..utils.error_handler import handle_exceptions
from ..utils.rate_limiter import RateLimiter

router = APIRouter(prefix="/skills", tags=["skills"])
skill_admin_limiter = RateLimiter(max_requests=20, window_seconds=60, scope="skill_registry_admin")


async def get_skill_registry() -> SkillRegistry:
    return SkillRegistry(await get_database())


async def get_policy_service() -> PolicyService:
    return PolicyService(await get_database())


def _user_id(current_user: CurrentUser) -> str:
    return str(getattr(current_user, "id", "") or "")


@router.get("", response_model=List[EffectiveSkill])
@handle_exceptions
async def list_effective_skills(
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
    registry: SkillRegistry = Depends(get_skill_registry),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Skills in force for an organisation and project, with pinned version and supported roles."""
    organization_id = organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        "drafting.skill.read",
        resource_type="skill",
        organization_id=organization_id,
        project_id=project_id,
    )
    return await registry.list_effective(organization_id, project_id)


@router.get("/admin", response_model=SkillAdminListResponse)
@handle_exceptions
async def list_skills_for_admin(
    organization_id: Optional[str] = None,
    registry: SkillRegistry = Depends(get_skill_registry),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Every skill and version, including drafts, plus bindings."""
    await policy.authorize(current_user, "drafting.skill.publish", resource_type="skill")
    return await registry.admin_list(organization_id)


@router.get("/{skill_key}/versions/{version}", response_model=SkillVersion)
@handle_exceptions
async def get_skill_version(
    skill_key: str,
    version: str,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
    registry: SkillRegistry = Depends(get_skill_registry),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Full text, references, manifest and hash of one version."""
    organization_id = organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        "drafting.skill.read",
        resource_type="skill",
        resource_id=skill_key,
        organization_id=organization_id,
        project_id=project_id,
    )
    record = await registry.get_version(skill_key, version)
    if record is None or record.status == "draft":
        # Drafts are unpublished, unreviewed text: not visible through the drafter route.
        raise HTTPException(status_code=404, detail="Skill version not found")
    return record


@router.post("/import", response_model=SkillVersion, status_code=201)
@handle_exceptions
async def import_skill_bundle(
    file: UploadFile = File(...),
    version: str = Form(...),
    kind: str = Form(...),
    manifest: str = Form(...),
    registry: SkillRegistry = Depends(get_skill_registry),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload a zip as a DRAFT version. It is untrusted until published."""
    await policy.authorize(current_user, "drafting.skill.publish", resource_type="skill")
    await skill_admin_limiter.check_user_limit(_user_id(current_user))
    data = await file.read(MAX_BUNDLE_BYTES + 1)
    try:
        options = SkillImportOptions(
            version=version, kind=kind, manifest=SkillManifest(**json.loads(manifest))
        )
        loaded = load_skill_zip(data)
    except (SkillBundleError, json.JSONDecodeError, PydanticValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"Bundle rejected: {str(exc)[:500]}") from exc
    guardrails = AIOutputGuardrailService.from_settings(settings)
    findings = guardrails.scan_evidence([loaded.skill_md, *[ref.text for ref in loaded.references]])
    return await registry.create_draft(
        loaded,
        version=options.version,
        kind=options.kind,
        manifest=options.manifest,
        created_by=_user_id(current_user),
        guardrail_findings=[finding.model_dump(mode="json") for finding in findings],
    )


@router.post("/{skill_key}/versions/{version}/publish", response_model=SkillVersion)
@handle_exceptions
async def publish_skill_version(
    skill_key: str,
    version: str,
    payload: SkillPublishRequest,
    registry: SkillRegistry = Depends(get_skill_registry),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Publish a draft once its evaluation report passes. Published versions are immutable."""
    await policy.authorize(
        current_user, "drafting.skill.publish", resource_type="skill", resource_id=skill_key
    )
    await skill_admin_limiter.check_user_limit(_user_id(current_user))
    return await registry.publish(skill_key, version, payload.eval_report, _user_id(current_user))


@router.post("/{skill_key}/versions/{version}/deprecate", response_model=SkillVersion)
@handle_exceptions
async def deprecate_skill_version(
    skill_key: str,
    version: str,
    registry: SkillRegistry = Depends(get_skill_registry),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Stop new use of a version. Runs that used it stay readable."""
    await policy.authorize(
        current_user, "drafting.skill.publish", resource_type="skill", resource_id=skill_key
    )
    await skill_admin_limiter.check_user_limit(_user_id(current_user))
    return await registry.deprecate(skill_key, version, _user_id(current_user))


@router.put("/bindings", response_model=SkillBinding)
@handle_exceptions
async def set_skill_binding(
    payload: SkillBindingRequest,
    registry: SkillRegistry = Depends(get_skill_registry),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Pin a version, or turn a skill off, for an organisation or project."""
    await policy.authorize(
        current_user,
        "drafting.skill.bind",
        resource_type="skill_binding",
        resource_id=payload.skill_key,
        organization_id=payload.organization_id,
        project_id=payload.project_id,
    )
    await skill_admin_limiter.check_user_limit(_user_id(current_user))
    return await registry.set_binding(payload, _user_id(current_user))
