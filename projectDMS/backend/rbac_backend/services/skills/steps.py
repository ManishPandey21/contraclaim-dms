"""Run and store skill steps for subjects other than letters.

One entry point, :meth:`SkillStepRunner.run`, does the whole of it:

- resolves the skill for the organisation and project;
- when no published version exists (a draft awaiting approval, a skill turned
  off), does NOT call a model: it stores the caller's deterministic fallback
  as a degraded step whose ``error`` gives the reason. An unreviewed skill is
  never used, and its absence is visible rather than silent;
- otherwise reuses a completed, non-degraded step with the same inputs, or
  runs the step through the executor (strict model call, schema validation,
  deterministic fallback on failure);
- stores the result on an idempotency key so a replay never duplicates.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence

from fastapi import HTTPException
from pydantic import BaseModel

from ...models.skill import SkillRunStep, SkillSubjectType
from ...utils.datetime import now_utc
from .compiler import SkillRoleNotSupported, overlay_key_for
from .executor import SkillExecutor, canonical_hash, step_input_hash
from .registry import SkillRegistry

SKILL_STEPS = "skill_steps"
UNAVAILABLE_HASH = "unavailable"


class SkillStepRunner:
    def __init__(self, db: Any, registry: SkillRegistry, executor: SkillExecutor) -> None:
        self.db = db
        self.registry = registry
        self.executor = executor

    @property
    def _steps(self) -> Any:
        return self.db[SKILL_STEPS]

    async def list_for_parent(self, parent_id: str) -> List[SkillRunStep]:
        docs = await self._steps.find({"parent_id": str(parent_id)}).to_list(length=2000)
        steps = [SkillRunStep(**doc) for doc in docs]
        steps.sort(key=lambda step: step.created_at)
        return steps

    async def get(self, step_id: str) -> Optional[SkillRunStep]:
        doc = await self._steps.find_one({"step_id": step_id})
        return SkillRunStep(**doc) if doc else None

    async def mark_stale(self, step_ids: Sequence[str]) -> None:
        ids = [step_id for step_id in step_ids if step_id]
        if ids:
            await self._steps.update_many({"step_id": {"$in": ids}}, {"$set": {"stale": True}})

    async def _put(self, step: SkillRunStep) -> SkillRunStep:
        key = {
            "subject_type": step.subject_type,
            "subject_id": step.subject_id,
            "skill_key": step.skill_key,
            "skill_hash": step.skill_hash,
            "input_hash": step.input_hash,
        }
        existing = await self._steps.find_one(key)
        if existing:
            step.step_id = existing["step_id"]
        await self._steps.update_one(
            key, {"$set": step.model_dump(by_alias=True, exclude={"id"})}, upsert=True
        )
        return step

    async def run(
        self,
        *,
        skill_key: str,
        subject_type: SkillSubjectType,
        subject_id: str,
        parent_id: Optional[str],
        organization_id: Optional[str],
        project_id: Optional[str],
        role: str,
        prompt_key: str,
        inputs: Dict[str, Any],
        payload: Dict[str, str],
        fallback: Callable[[], BaseModel],
        schema_key: str,
        user_id: Optional[str],
        force: bool = False,
    ) -> SkillRunStep:
        version, _binding, reason = await self.registry.resolve(skill_key, organization_id, project_id)
        base = {
            "step_id": uuid.uuid4().hex,
            "subject_type": subject_type,
            "subject_id": str(subject_id),
            "parent_id": str(parent_id) if parent_id else None,
            "organization_id": organization_id,
            "project_id": project_id,
            "skill_key": skill_key,
            "role": role,
            "output_schema_key": schema_key,
            "prompt_key": prompt_key,
            "created_by": user_id,
            "completed_at": now_utc(),
        }
        if version is None:
            return await self._put(
                SkillRunStep(
                    **base,
                    skill_hash=UNAVAILABLE_HASH,
                    input_hash=canonical_hash({"role": role, "schema_key": schema_key, "inputs": inputs}),
                    output=fallback().model_dump(mode="json"),
                    degraded=True,
                    error=f"Skill not used: {reason or 'No published version'}",
                )
            )
        input_hash = step_input_hash(
            skill_key=version.skill_key,
            skill_hash=version.content_hash,
            role=role,
            overlay_key=overlay_key_for(role),
            schema_key=schema_key,
            inputs=inputs,
        )
        if not force:
            cached = await self._steps.find_one(
                {
                    "subject_type": subject_type,
                    "subject_id": str(subject_id),
                    "skill_key": version.skill_key,
                    "skill_hash": version.content_hash,
                    "input_hash": input_hash,
                }
            )
            if cached and cached.get("status") == "completed" and not cached.get("degraded"):
                if cached.get("stale"):
                    await self._steps.update_one({"step_id": cached["step_id"]}, {"$set": {"stale": False}})
                    cached["stale"] = False
                return SkillRunStep(**cached)
        try:
            result = await self.executor.run(
                version=version,
                role=role,
                prompt_key=prompt_key,
                payload=payload,
                fallback=fallback,
                schema_key=schema_key,
            )
        except SkillRoleNotSupported as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return await self._put(
            SkillRunStep(
                **base,
                skill_version=version.version,
                skill_hash=version.content_hash,
                skill_name=version.name,
                overlay_key=result.compiled.overlay_key,
                input_hash=input_hash,
                output=result.output,
                degraded=result.degraded,
                error=result.error,
                model=result.model,
                prompt_version=result.prompt_version,
            )
        )


__all__ = ["SKILL_STEPS", "UNAVAILABLE_HASH", "SkillStepRunner"]
