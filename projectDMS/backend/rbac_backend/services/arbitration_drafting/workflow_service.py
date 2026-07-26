from __future__ import annotations

import uuid
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, Optional

from fastapi import HTTPException

from ...core.config import settings
from ...models.arbitration_drafting import (
    ArbitrationProductionAcceptanceRequest,
    ArbitrationWorkflowApprovalRequest,
    ArbitrationWorkflowCreateRequest,
)
from .case_workspace import ArbitrationCaseWorkspaceService, _actor_id
from .engines import ArbitrationEngineSelector, ArbitrationV2WorkflowEngine, canonical_workflow_request_hash
from .workflow_repository import ArbitrationWorkflowRepository, artifact_hash
from .workflow_domain import ArbitrationWorkflowDomain
from .approval_policy import enforce_author_approver_separation, enforce_gate_role
from ...models.arbitration_drafting import ArbitrationReadinessApprovalRequest, ArbitrationGenerateRequest
from .service import ArbitrationDraftingService, immutable_version_hash
from .workflow_validation import ArbitrationValidationOrchestrator
from .workflow_hardening import build_rollout_health, build_shadow_comparison
from .repository import _collect
from ..observability import observability_registry
from .acceptance import (
    acceptance_hash,
    resolve_server_backed_acceptance,
    sign_acceptance,
    verify_acceptance_receipt,
)


GATE_ARTIFACT_FIELDS = {
    "document_selection": "document_manifest_hash",
    "matrix_review": "matrix_revision_hash",
    "readiness": "readiness_artifact_hash",
    "plan": "plan_hash",
    "legal_review": "draft_version_hash",
    "draft": "draft_version_hash",
    "export": "draft_version_hash",
}

GATE_TRANSITIONS = {
    "document_selection": ("running", "analyze_documents", "poll", 20),
    "matrix_review": ("awaiting_readiness_approval", "readiness_approval_gate", "approve_readiness", 45),
    "readiness": ("awaiting_plan_approval", "plan_approval_gate", "approve_plan", 60),
    "plan": ("running", "generate_draft", "poll", 70),
    "legal_review": ("awaiting_draft_approval", "draft_approval_gate", "approve_draft", 88),
    "draft": ("awaiting_export_authorization", "export_authorization_gate", "authorize_export", 95),
    "export": ("completed", "complete", "completed", 100),
}

RESUMABLE_GATES = {
    "document_selection_gate": "document_selection",
    "material_question_gate": "material_question",
    "matrix_review_gate": "matrix_review",
    "legal_review_gate": "legal_review",
}

TERMINAL_WORKFLOW_STATUSES = {"completed", "cancelled", "failed"}


class ArbitrationWorkflowService:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.repository = ArbitrationWorkflowRepository(db)
        self.cases = ArbitrationCaseWorkspaceService(db)
        self.domain = ArbitrationWorkflowDomain(db)
        self.drafting = ArbitrationDraftingService(db)
        self.validation = ArbitrationValidationOrchestrator()

    @staticmethod
    def _shadow_vector(
        run: Dict[str, Any],
        *,
        matrix_revision_hash: Optional[str] = None,
        readiness_artifact_hash: Optional[str] = None,
        blockers: Optional[list[Dict[str, Any]]] = None,
        questions: Optional[list[Dict[str, Any]]] = None,
        current_node: Optional[str] = None,
        evidence_set_hash: Optional[str] = None,
        section_coverage_hash: Optional[str] = None,
        citation_validity_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        blocker_rows = blockers if blockers is not None else list(run.get("blockers") or [])
        question_rows = questions if questions is not None else list(run.get("targeted_questions") or [])
        validation_blockers = list(run.get("validation_blockers") or [])
        approval_gates = sorted(
            gate
            for gate in GATE_ARTIFACT_FIELDS
            if run.get(f"{gate}_approval_receipt_id")
        )
        return {
            "evidence_set": evidence_set_hash or run.get("evidence_snapshot_hash"),
            "matrix_rows": matrix_revision_hash or run.get("matrix_revision_hash"),
            "readiness": readiness_artifact_hash or run.get("readiness_artifact_hash"),
            "section_coverage": section_coverage_hash,
            "citation_validity": citation_validity_hash,
            "validation_blockers": artifact_hash(
                sorted(
                    (str(item.get("branch") or "unknown"), str(item.get("code") or "unknown"))
                    for item in validation_blockers
                )
            ) if run.get("validation_status") else None,
            "human_interventions": artifact_hash(
                {
                    "blockers": sorted(str(item.get("code") or "unknown") for item in blocker_rows),
                    "questions": sorted(str(item.get("question_id") or "unknown") for item in question_rows),
                    "approval_gates": approval_gates,
                    "current_node": current_node or run.get("current_node"),
                }
            ),
        }

    async def _shadow_artifact_metrics(self, run: Dict[str, Any]) -> Dict[str, Optional[str]]:
        evidence_set_hash = None
        if run.get("evidence_snapshot_id"):
            snapshot = await self.db.arbitration_workflow_snapshots.find_one(
                {
                    "_id": run.get("evidence_snapshot_id"),
                    "run_id": str(run["_id"]),
                    "kind": "evidence_manifest",
                }
            )
            if snapshot:
                evidence_set_hash = artifact_hash((snapshot.get("payload") or {}).get("evidence") or [])

        plan = None
        if run.get("plan_id"):
            plan = await self.db.arbitration_plans.find_one(
                {"_id": run.get("plan_id"), "run_id": str(run["_id"])}
            )
        version = None
        if run.get("draft_version_id") and run.get("draft_id"):
            version = await self.db.arbitration_draft_versions.find_one(
                {"_id": run.get("draft_version_id"), "draft_id": str(run.get("draft_id"))}
            )
        section_coverage_hash = None
        if plan or version:
            planned = [
                str(item.get("key") or "")
                for item in (plan or {}).get("section_structure") or []
                if isinstance(item, dict) and item.get("key")
            ]
            raw_sections = (version or {}).get("sections") or {}
            if isinstance(raw_sections, dict):
                drafted = sorted(str(value) for value in raw_sections)
            else:
                drafted = sorted(
                    str(item.get("section_key") or item.get("title") or index)
                    if isinstance(item, dict)
                    else str(index)
                    for index, item in enumerate(raw_sections)
                )
            section_coverage_hash = artifact_hash(
                {
                    "planned": planned,
                    "drafted": drafted,
                    "missing": sorted(set(planned).difference(drafted)),
                    "source_mapped": sorted(
                        str(item.get("section_key"))
                        for item in (plan or {}).get("section_source_mapping") or []
                        if isinstance(item, dict) and item.get("section_key")
                    ),
                }
            )

        citation_validity_hash = None
        if run.get("validation_status"):
            report = None
            if run.get("validation_report_id"):
                report = await self.db.arbitration_workflow_snapshots.find_one(
                    {
                        "_id": run.get("validation_report_id"),
                        "run_id": str(run["_id"]),
                        "kind": "validation_report",
                    }
                )
            validation_blockers = list(run.get("validation_blockers") or [])
            citation_validity_hash = artifact_hash(
                {
                    "status": run.get("validation_status"),
                    "validation_input_hash": ((report or {}).get("payload") or {}).get("validation_input_hash"),
                    "citation_blocker_codes": sorted(
                        str(item.get("code") or "unknown")
                        for item in validation_blockers
                        if str(item.get("branch") or "") in {"citations", "exhibits", "source_drift"}
                    ),
                }
            )
        return {
            "evidence_set_hash": evidence_set_hash,
            "section_coverage_hash": section_coverage_hash,
            "citation_validity_hash": citation_validity_hash,
        }

    async def _record_shadow_comparison(
        self,
        run: Dict[str, Any],
        *,
        authoritative_latency_ms: Optional[float] = None,
    ) -> None:
        if str(run.get("rollout_mode")) != "shadow":
            return
        effect_key = f"{run['_id']}:shadow-comparison:{int(run.get('state_version') or 0)}"
        existing = await self.db.arbitration_workflow_snapshots.find_one({"effect_key": effect_key})
        if existing:
            return
        started = perf_counter()
        from .shadow_candidate import ArbitrationShadowCandidate

        candidate_analysis = await ArbitrationShadowCandidate(self.db).analyze(run)
        from .langgraph_engine import LangGraphArbitrationEngine

        graph_projection = await LangGraphArbitrationEngine(self.db).shadow_route_projection(run)
        case = await self.cases.get_case(str(run["case_id"]))
        candidate_readiness = await self.cases._readiness_artifact_state(case, str(run.get("pleading_type")))
        candidate_questions = self.domain.material_questions(
            candidate_analysis.get("blockers") or [], str(run.get("pleading_type"))
        )
        artifact_metrics = await self._shadow_artifact_metrics(run)
        candidate_latency_ms = (perf_counter() - started) * 1000
        authoritative = self._shadow_vector(run, **artifact_metrics)
        candidate = self._shadow_vector(
            run,
            matrix_revision_hash=candidate_analysis.get("revision_hash"),
            readiness_artifact_hash=candidate_readiness.get("artifact_hash"),
            blockers=candidate_analysis.get("blockers") or [],
            questions=candidate_questions,
            current_node=str(graph_projection.get("projected_node") or "unknown"),
            **artifact_metrics,
        )
        comparison = build_shadow_comparison(
            pleading_type=str(run.get("pleading_type")),
            state_version=int(run.get("state_version") or 0),
            authoritative=authoritative,
            candidate=candidate,
            authoritative_latency_ms=authoritative_latency_ms,
            candidate_latency_ms=candidate_latency_ms if authoritative_latency_ms is not None else None,
        )
        snapshot = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="shadow_comparison",
            payload=comparison,
            effect_key=effect_key,
        )
        await self.repository.append_event(
            str(run["_id"]),
            "shadow_comparison",
            data={
                "comparison_hash": comparison["comparison_hash"],
                "snapshot_id": snapshot["_id"],
                "overall_status": comparison["overall_status"],
                "evaluated_dimensions": comparison["evaluated_dimensions"],
                "mismatch_dimensions": comparison["mismatch_dimensions"],
                "authoritative_writes": False,
            },
        )
        await observability_registry.record_arbitration_shadow_comparison(comparison)

    async def _record_shadow_safely(
        self,
        run: Dict[str, Any],
        *,
        authoritative_latency_ms: Optional[float] = None,
    ) -> None:
        try:
            await self._record_shadow_comparison(run, authoritative_latency_ms=authoritative_latency_ms)
        except Exception as exc:
            failure_key = (
                f"{run['_id']}:shadow-comparison-failure:"
                f"{int(run.get('state_version') or 0)}:{type(exc).__name__}"
            )
            if await self.db.arbitration_workflow_snapshots.find_one({"effect_key": failure_key}):
                return
            failure = await self.repository.create_snapshot(
                run_id=str(run["_id"]),
                kind="shadow_comparison_failure",
                payload={
                    "schema_version": 1,
                    "state_version": int(run.get("state_version") or 0),
                    "error_code": type(exc).__name__,
                    "authoritative_writes": False,
                },
                effect_key=failure_key,
            )
            await self.repository.append_event(
                str(run["_id"]),
                "shadow_comparison_failed",
                data={
                    "error_code": type(exc).__name__,
                    "snapshot_id": failure["_id"],
                    "authoritative_writes": False,
                },
            )
            await observability_registry.record_arbitration_workflow(
                engine="langgraph_v1", status="shadow_failed", node=str(run.get("current_node")), event="shadow_failed"
            )

    @staticmethod
    def _validation_dependencies(run: Dict[str, Any]) -> Dict[str, str]:
        return {
            key: str(run.get(key))
            for key in (
                "input_snapshot_hash",
                "document_manifest_hash",
                "evidence_snapshot_hash",
                "opponent_pleading_snapshot_hash",
                "matrix_revision_hash",
                "readiness_artifact_hash",
                "plan_hash",
            )
            if run.get(key)
        }

    async def _persist_validation(
        self,
        run: Dict[str, Any],
        version: Dict[str, Any],
        *,
        remediation_cycle: int,
    ) -> Dict[str, Any]:
        report = await self.validation.evaluate(
            version,
            dependency_hashes=self._validation_dependencies(run),
            remediation_cycle=remediation_cycle,
        )
        artifact_refs = []
        for artifact in report.get("artifacts") or []:
            branch = str(artifact["branch"])
            snapshot = await self.repository.create_snapshot(
                run_id=str(run["_id"]),
                kind=f"validation_{branch}",
                payload=artifact,
                effect_key=(
                    f"{run['_id']}:validation:{report['validation_input_hash']}:"
                    f"{branch}:{artifact['artifact_hash']}"
                ),
            )
            artifact_refs.append(
                {
                    "branch": branch,
                    "snapshot_id": snapshot["_id"],
                    "snapshot_hash": snapshot["snapshot_hash"],
                    "artifact_hash": artifact["artifact_hash"],
                    "status": artifact["status"],
                }
            )
        report_snapshot = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="validation_report",
            payload={
                key: value
                for key, value in report.items()
                if key not in {"artifacts"}
            },
            effect_key=f"{run['_id']}:validation-report:{report['validation_input_hash']}:{report['report_hash']}",
        )
        stable_artifact_refs = [
            {
                "branch": item["branch"],
                "artifact_hash": item["artifact_hash"],
                "status": item["status"],
            }
            for item in sorted(artifact_refs, key=lambda item: item["branch"])
        ]
        artifact_set_hash = artifact_hash(
            {
                "schema_version": report["schema_version"],
                "validation_input_hash": report["validation_input_hash"],
                "version_hash": report["version_hash"],
                "report_hash": report["report_hash"],
                "artifacts": stable_artifact_refs,
            }
        )
        artifact_set_payload = {
            "schema_version": report["schema_version"],
            "validation_input_hash": report["validation_input_hash"],
            "version_hash": report["version_hash"],
            "report_hash": report["report_hash"],
            "artifact_set_hash": artifact_set_hash,
            "report_snapshot_id": report_snapshot["_id"],
            "report_snapshot_hash": report_snapshot["snapshot_hash"],
            "artifacts": sorted(artifact_refs, key=lambda item: item["branch"]),
            "authoritative": False,
        }
        artifact_set = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="validation_artifact_set",
            payload=artifact_set_payload,
            effect_key=f"{run['_id']}:validation-set:{report['validation_input_hash']}:{report['report_hash']}",
        )
        return {
            **report,
            "validation_report_id": report_snapshot["_id"],
            "validation_report_snapshot_hash": report_snapshot["snapshot_hash"],
            "validation_artifact_set_id": artifact_set["_id"],
            "validation_artifact_set_hash": artifact_set_hash,
            "validation_artifact_set_snapshot_hash": artifact_set["snapshot_hash"],
            "validation_artifact_refs": artifact_refs,
        }

    async def _create_remediated_candidate(
        self,
        run: Dict[str, Any],
        version: Dict[str, Any],
        report: Dict[str, Any],
        current_user: Any,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        remediation = self.validation.remediate(version, report)
        if not remediation:
            return version, report
        cycle = int(report.get("remediation_cycle") or 0) + 1
        input_hash = artifact_hash(
            {
                "policy": report.get("remediation_policy"),
                "report_hash": report.get("report_hash"),
                "version_hash": report.get("version_hash"),
                "cycle": cycle,
            }
        )
        effect_key = f"{run['_id']}:remediate:{input_hash}"
        effect = await self.repository.claim_effect(
            run_id=str(run["_id"]),
            effect_key=effect_key,
            effect_type="draft_remediation",
            input_hash=input_hash,
        )
        if effect.get("status") == "completed":
            output = effect.get("output_refs") or {}
            remediated = await self.db.arbitration_draft_versions.find_one(
                {"_id": output.get("draft_version_id"), "draft_id": str(run["draft_id"])}
            )
            if not remediated:
                raise HTTPException(status_code=409, detail="Completed remediation effect references a missing immutable version")
            remediated_hash = remediated.get("version_hash") or immutable_version_hash(remediated)
            if remediated_hash != output.get("draft_version_hash"):
                raise HTTPException(status_code=409, detail="Completed remediation effect output hash does not match its immutable version")
        else:
            if not effect.get("_claimed_now"):
                if effect.get("_attempt_limit_reached"):
                    raise HTTPException(
                        status_code=409,
                        detail="Draft remediation effect exhausted its recovery attempt budget",
                    )
                raise HTTPException(status_code=409, detail="Draft remediation effect is already in progress")
            effect_lease_token = str(effect.get("lease_token") or "")
            if not effect_lease_token:
                raise HTTPException(status_code=409, detail="Draft remediation effect lease token is missing")
            try:
                remediation_version_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"arbitration-remediation:{run['_id']}:{input_hash}",
                    )
                )
                remediated = await self.db.arbitration_draft_versions.find_one(
                    {"_id": remediation_version_id, "draft_id": str(run["draft_id"])}
                )
                if remediated:
                    if str(remediated.get("parent_version_id") or "") != str(version.get("_id") or ""):
                        raise HTTPException(
                            status_code=409,
                            detail="Recovered remediation version has a different immutable parent",
                        )
                    calculated_hash = immutable_version_hash(remediated)
                    remediated_hash = remediated.get("version_hash") or calculated_hash
                    if remediated.get("version_hash") and remediated["version_hash"] != calculated_hash:
                        raise HTTPException(
                            status_code=409,
                            detail="Recovered remediation version failed its immutable hash check",
                        )
                    version_number = int(remediated.get("version") or 0)
                else:
                    version_number = await self.drafting.repo.next_version(str(run["draft_id"]))
                    structured = {
                        **(version.get("structured_output") or {}),
                        "workflow_remediation": {
                            "policy": report.get("remediation_policy"),
                            "cycle": cycle,
                            "applied_fixes": remediation["applied_fixes"],
                            "parent_version_hash": report.get("version_hash"),
                        },
                    }
                    remediated = {
                        **version,
                        "_id": remediation_version_id,
                        "version": version_number,
                        "sections": remediation["sections"],
                        "full_markdown": remediation["full_markdown"],
                        "structured_output": structured,
                        "parent_version_id": version.get("_id"),
                        "parent_version": version.get("version"),
                        "generation_run_id": None,
                        "created_by": _actor_id(current_user),
                        "created_at": datetime.now(timezone.utc),
                    }
                    remediated["version_hash"] = immutable_version_hash(remediated)
                    remediated = await self.drafting.repo.create_version(remediated)
                await self.drafting.repo.update_draft(
                    str(run["draft_id"]),
                    {
                        "current_version": version_number,
                        "updated_at": datetime.now(timezone.utc),
                        "updated_by": _actor_id(current_user),
                    },
                )
                remediated_hash = remediated["version_hash"]
                await self.repository.complete_effect(
                    effect_key,
                    {"draft_version_id": remediated["_id"], "draft_version_hash": remediated_hash},
                    lease_token=effect_lease_token,
                )
            except Exception as exc:
                await self.repository.fail_effect(
                    effect_key,
                    error_code=type(exc).__name__,
                    lease_token=effect_lease_token,
                )
                raise
        remediation_artifact = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="draft_remediation",
            payload={
                "policy": report.get("remediation_policy"),
                "cycle": cycle,
                "parent_version_id": version.get("_id"),
                "parent_version_hash": report.get("version_hash"),
                "draft_version_id": remediated.get("_id"),
                "draft_version_hash": remediated_hash,
                "applied_fixes": remediation["applied_fixes"],
                "before_content_hash": remediation["before_content_hash"],
                "after_content_hash": remediation["after_content_hash"],
                "source_keys_preserved": remediation["source_keys_preserved"],
                "amounts_preserved": remediation["amounts_preserved"],
                "dates_preserved": remediation["dates_preserved"],
            },
            effect_key=f"{run['_id']}:remediation-artifact:{input_hash}",
        )
        return remediated, {
            "remediation_artifact_id": remediation_artifact["_id"],
            "remediation_artifact_hash": remediation_artifact["snapshot_hash"],
            "remediation_cycle": cycle,
        }

    async def _validate_with_bounded_remediation(
        self,
        run: Dict[str, Any],
        version: Dict[str, Any],
        current_user: Any,
    ) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        cycle = 0
        remediation_refs: Dict[str, Any] = {}
        report = await self._persist_validation(run, version, remediation_cycle=cycle)
        while report.get("route") == "remediate":
            remediated, refs = await self._create_remediated_candidate(run, version, report, current_user)
            if remediated.get("_id") == version.get("_id"):
                report = self.validation.bounded_remediation(
                    {**report, "remediation_candidates": [], "termination_reason": "no_safe_automatic_remediation"}
                )
                break
            version = remediated
            remediation_refs = refs
            cycle = int(refs["remediation_cycle"])
            report = await self._persist_validation(run, version, remediation_cycle=cycle)
        return version, report, remediation_refs

    @staticmethod
    def _assert_state_version(run: Dict[str, Any], expected_version: int) -> None:
        current_version = int(run.get("state_version") or 0)
        if current_version != int(expected_version):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Stale arbitration workflow state version",
                    "current_state_version": current_version,
                },
            )

    async def _sync_langgraph_checkpoint(
        self,
        run: Dict[str, Any],
        update: Dict[str, Any],
        *,
        traverse: bool = True,
    ) -> None:
        if run.get("engine") != "langgraph_v1":
            return
        from .langgraph_engine import LangGraphArbitrationEngine

        try:
            engine = LangGraphArbitrationEngine(self.db)
            if traverse:
                await engine.checkpoint_transition(run, update)
            else:
                await engine.checkpoint_state_update(run, update)
        except Exception as exc:
            run.update(
                {
                    "checkpoint_sync_status": "pending",
                    "checkpoint_sync_state_version": run.get("state_version"),
                    "checkpoint_sync_error_code": type(exc).__name__,
                }
            )
            await self.db.arbitration_workflow_runs.update_one(
                {"_id": run["_id"], "state_version": run.get("state_version")},
                {
                    "$set": {
                        "checkpoint_sync_status": "pending",
                        "checkpoint_sync_state_version": run.get("state_version"),
                        "checkpoint_sync_error_code": type(exc).__name__,
                    }
                },
            )
            await self.repository.append_event(
                run["_id"],
                "checkpoint_sync_pending",
                data={"state_version": run.get("state_version"), "error_code": type(exc).__name__},
            )
            return
        run.update(
            {
                "checkpoint_sync_status": "synced",
                "checkpoint_sync_state_version": run.get("state_version"),
                "checkpoint_sync_error_code": None,
            }
        )
        await self.db.arbitration_workflow_runs.update_one(
            {"_id": run["_id"], "state_version": run.get("state_version")},
            {
                "$set": {
                    "checkpoint_sync_status": "synced",
                    "checkpoint_sync_state_version": run.get("state_version"),
                    "checkpoint_sync_error_code": None,
                }
            },
        )

    async def create(
        self,
        case_id: str,
        payload: ArbitrationWorkflowCreateRequest,
        current_user: Any,
        *,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        case = await self.cases.get_case(case_id)
        request_hash = canonical_workflow_request_hash(
            case_id=case_id,
            payload=payload,
            tenant_id=str(case.get("organization_id") or ""),
            project_id=str(case.get("project_id") or ""),
        )
        rollout_health = None
        if str(settings.ARBITRATION_ENGINE_ROLLOUT_MODE or "off").lower() == "primary":
            rollout_health = await self._scoped_rollout_health(
                organization_id=str(case.get("organization_id") or ""),
                project_id=str(case.get("project_id") or ""),
            )
        decision = ArbitrationEngineSelector().select(
            tenant_id=str(case.get("organization_id") or ""),
            project_id=str(case.get("project_id") or ""),
            request_hash=request_hash,
            rollout_health=rollout_health,
        )
        if decision.engine == "unavailable":
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "Arbitration workflow creation is temporarily unavailable under the Phase 6 compatibility policy",
                    "reason": decision.reason,
                },
            )
        if decision.engine == "langgraph_v1":
            from .langgraph_engine import LangGraphArbitrationEngine

            engine = LangGraphArbitrationEngine(self.db)
        else:
            engine = ArbitrationV2WorkflowEngine(self.db)
        authoritative_started = perf_counter()
        run = await engine.create_workflow(
            case_id,
            payload,
            current_user,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            rollout_mode=decision.rollout_mode,
            rollout_decision={
                "policy_version": decision.policy_version,
                "reason": decision.reason,
                "decision_hash": decision.decision_hash,
                "acceptance_receipt_sha256": decision.acceptance_receipt_sha256,
                "v2_compatibility_mode": decision.v2_compatibility_mode,
            },
        )
        authoritative_latency_ms = (perf_counter() - authoritative_started) * 1000
        if payload.requested_engine and payload.requested_engine != decision.engine:
            await self.repository.append_event(
                run["_id"], "client_engine_request_ignored", actor_id=_actor_id(current_user),
                data={"requested": payload.requested_engine, "selected": decision.engine},
            )
        if decision.shadow:
            await self._record_shadow_safely(run, authoritative_latency_ms=authoritative_latency_ms)
        await observability_registry.record_arbitration_workflow(
            engine=str(run.get("engine")), status=str(run.get("status")), node=str(run.get("current_node")), event="created"
        )
        return self.public_state(run)

    async def get(self, case_id: str, run_id: str) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        await self.repository.reconcile_pending_approvals(run)
        return self.public_state(run)

    async def list(self, case_id: str, *, active_only: bool = False, limit: int = 25) -> list[Dict[str, Any]]:
        return [
            self.public_state(run)
            for run in await self.repository.list_case_runs(case_id, active_only=active_only, limit=limit)
        ]

    async def events(self, case_id: str, run_id: str) -> list[Dict[str, Any]]:
        await self.get(case_id, run_id)
        return await self.repository.list_events(run_id)

    async def approve_gate(
        self,
        case_id: str,
        run_id: str,
        gate: str,
        payload: ArbitrationWorkflowApprovalRequest,
        current_user: Any,
    ) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        await self.repository.reconcile_pending_approvals(run)
        await observability_registry.record_arbitration_runtime_event(
            signal="node_attempt", node=f"{gate}_approval_gate", reason="approval"
        )
        self._assert_state_version(run, payload.state_version)
        artifact_field = GATE_ARTIFACT_FIELDS.get(gate)
        transition = GATE_TRANSITIONS.get(gate)
        if not artifact_field or not transition:
            raise HTTPException(status_code=400, detail="Unknown arbitration workflow approval gate")
        if gate == "matrix_review":
            current_analysis = await self.domain.inspect_matrix_revision(run)
            if current_analysis["revision_hash"] != run.get("matrix_revision_hash"):
                await self.repository.invalidate_case_dependencies(
                    case_id,
                    reason="matrix_revision_hash_changed",
                    actor_id=_actor_id(current_user),
                )
                raise HTTPException(status_code=409, detail="Matrix revision set drifted; refresh the workflow before approval")
            if current_analysis["blockers"]:
                raise HTTPException(status_code=409, detail={"message": "Required pleading matrices are incomplete", "blockers": current_analysis["blockers"]})
        elif gate == "readiness":
            case = await self.cases.get_case(case_id)
            current_readiness = await self.cases._readiness_artifact_state(case, str(run.get("pleading_type")))
            if current_readiness["artifact_hash"] != run.get("readiness_artifact_hash"):
                await self.repository.invalidate_case_dependencies(
                    case_id,
                    reason="readiness_dependency_hash_changed",
                    actor_id=_actor_id(current_user),
                )
                raise HTTPException(status_code=409, detail="Readiness evidence or matrix state drifted; refresh the workflow")
        elif gate in {"legal_review", "draft", "export"}:
            if not run.get("draft_id"):
                raise HTTPException(status_code=409, detail="Workflow has no case-linked draft")
            current_version = await self.drafting.repo.latest_version(str(run["draft_id"]))
            current_hash = (current_version or {}).get("version_hash") or immutable_version_hash(current_version or {})
            if current_hash != run.get("draft_version_hash"):
                raise HTTPException(status_code=409, detail="Draft version drifted; review the current immutable version")
            if gate == "legal_review" and (
                run.get("validation_status") != "passed"
                or run.get("validation_blockers")
                or not run.get("validation_artifact_set_hash")
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Workflow validation blockers must be resolved in a new immutable candidate before legal approval",
                        "validation_status": run.get("validation_status"),
                        "blockers": run.get("validation_blockers") or [],
                    },
                )
        expected_hash = str(run.get(artifact_field) or "")
        if not expected_hash or expected_hash != payload.artifact_hash:
            raise HTTPException(status_code=409, detail="Approval artifact hash is stale or does not match the workflow state")
        expected_node = {
            "document_selection": "document_selection_gate", "matrix_review": "matrix_review_gate",
            "readiness": "readiness_approval_gate", "plan": "plan_approval_gate",
            "legal_review": "legal_review_gate", "draft": "draft_approval_gate", "export": "export_authorization_gate",
        }[gate]
        if str(run.get("current_node")) != expected_node:
            raise HTTPException(status_code=409, detail="Approval gate does not match the current workflow node")
        role = enforce_gate_role(gate, payload.reviewer_role, current_user)
        actor = _actor_id(current_user)
        enforce_author_approver_separation(
            current_user,
            [run.get("created_by"), run.get("last_material_editor_id"), (run.get("artifact_authors") or {}).get(artifact_field)],
            gate=gate,
        )
        receipt = {
            "_id": str(uuid.uuid4()),
            "run_id": run_id,
            "case_id": case_id,
            "draft_id": run.get("draft_id"),
            "gate": gate,
            "artifact_hash": payload.artifact_hash,
            "decision": payload.decision,
            "approver_id": actor,
            "approver_role": role,
            "comment": payload.comment,
            "approved_at": datetime.now(timezone.utc),
        }
        receipt = await self.repository.record_approval(receipt)
        if payload.decision != "approved":
            if gate == "plan":
                await self.db.arbitration_plans.update_one(
                    {"_id": run.get("plan_id"), "run_id": run_id, "plan_hash": run.get("plan_hash")},
                    {
                        "$set": {
                            "status": payload.decision,
                            "review_receipt_id": receipt["_id"],
                            "reviewed_at": datetime.now(timezone.utc),
                        }
                    },
                )
            rejection_update = (
                {
                    "status": "awaiting_legal_review",
                    "current_node": "legal_review_gate",
                    "next_action": "revise_draft",
                    "required_human_role": "drafter",
                }
                if gate in {"legal_review", "draft"}
                else {
                    "status": "awaiting_matrix_review",
                    "current_node": "matrix_review_gate",
                    "next_action": "review_matrices",
                    "required_human_role": "legal_reviewer",
                }
            )
            updated = await self.repository.transition(
                run_id,
                payload.state_version,
                rejection_update,
                event=f"{gate}_{payload.decision}",
            )
            await self.repository.commit_approval(str(receipt["_id"]), run_id)
            await self._record_shadow_safely(updated)
            return self.public_state(updated)
        if run.get("engine") == "langgraph_v1":
            status_value, node, next_action, progress = transition
            if gate == "export":
                # Authorization is a human receipt; completion belongs to the
                # create_filing_export graph node after its idempotent effect.
                status_value, node, next_action, progress = (
                    "running",
                    "create_filing_export",
                    "poll",
                    97,
                )
            updated = await self.repository.transition(
                run_id,
                payload.state_version,
                {
                    "status": status_value,
                    "current_node": node,
                    "next_action": next_action,
                    "progress": progress,
                    "required_human_role": (
                        None
                        if status_value in {"running", "completed"}
                        else "legal_reviewer"
                        if node == "legal_review_gate"
                        else "senior_legal"
                    ),
                    f"{gate}_approval_receipt_id": receipt["_id"],
                    "authoritative_effects": [
                        *(run.get("authoritative_effects") or []),
                        f"{gate}_approval",
                    ],
                },
                event=f"{gate}_approved",
            )
            await self.repository.commit_approval(str(receipt["_id"]), run_id)
            flag = {
                "document_selection": "documents_selected",
                "matrix_review": "matrices_approved",
                "readiness": "readiness_approved",
                "plan": "plan_approved",
                "legal_review": "legal_review_approved",
                "draft": "draft_approved",
                "export": "export_authorized",
            }[gate]
            from .langgraph_engine import LangGraphArbitrationEngine

            await LangGraphArbitrationEngine(self.db).checkpoint_transition(updated, {flag: True})
            final = await self.repository.get_run(run_id)
            if not final:
                raise HTTPException(status_code=404, detail="Arbitration workflow run not found after graph approval")
            await self._record_shadow_safely(final)
            return self.public_state(final)
        status_value, node, next_action, progress = transition
        transition_update: Dict[str, Any] = {}
        if gate == "readiness":
            readiness = await self.cases.approve_readiness(
                case_id,
                current_user,
                ArbitrationReadinessApprovalRequest(
                    draft_id=run.get("draft_id"),
                    draft_type=run.get("pleading_type"),
                    reviewer_role="senior_legal_approver",
                    comment=payload.comment,
                ),
            )
            plan = await self.domain.build_plan(run, current_user)
            transition_update.update(
                {
                    "readiness_approval_receipt_id": (readiness.get("approval_receipt") or {}).get("_id"),
                    "plan_id": plan["_id"], "plan_hash": plan["plan_hash"],
                    "artifact_authors": {**(run.get("artifact_authors") or {}), "plan_hash": plan.get("created_by")},
                }
            )
        elif gate == "plan":
            if not run.get("draft_id"):
                raise HTTPException(status_code=409, detail="A case-linked draft is required before the approved plan can be generated")
            plan = await self.db.arbitration_plans.find_one(
                {"_id": run.get("plan_id"), "run_id": run_id, "plan_hash": run.get("plan_hash")}
            )
            if not plan:
                raise HTTPException(status_code=409, detail="Approved pleading plan artifact is missing or has drifted")
            await self.db.arbitration_plans.update_one(
                {"_id": plan["_id"], "run_id": run_id, "plan_hash": run.get("plan_hash")},
                {
                    "$set": {
                        "status": "approved",
                        "approval_receipt_id": receipt["_id"],
                        "approved_at": datetime.now(timezone.utc),
                    }
                },
            )
            plan = {
                **plan,
                "status": "approved",
                "approval_receipt_id": receipt["_id"],
                "approved_at": datetime.now(timezone.utc),
            }
            effect_key = f"{run_id}:generate:{run['plan_hash']}"
            effect = await self.repository.claim_effect(
                run_id=run_id,
                effect_key=effect_key,
                effect_type="draft_generation",
                input_hash=run["plan_hash"],
            )
            if effect.get("status") == "completed":
                output_refs = effect.get("output_refs") or {}
                version = await self.db.arbitration_draft_versions.find_one(
                    {"_id": output_refs.get("draft_version_id"), "draft_id": str(run["draft_id"])}
                )
                if not version:
                    raise HTTPException(status_code=409, detail="Completed draft effect references a missing immutable version")
                version_hash = version.get("version_hash") or immutable_version_hash(version)
                if version_hash != output_refs.get("draft_version_hash"):
                    raise HTTPException(status_code=409, detail="Completed draft effect output hash does not match its immutable version")
            else:
                if not effect.get("_claimed_now"):
                    if effect.get("_attempt_limit_reached"):
                        raise HTTPException(
                            status_code=409,
                            detail="Draft generation effect exhausted its recovery attempt budget",
                        )
                    raise HTTPException(status_code=409, detail="Draft generation effect is already in progress")
                effect_lease_token = str(effect.get("lease_token") or "")
                if not effect_lease_token:
                    raise HTTPException(status_code=409, detail="Draft generation effect lease token is missing")
                try:
                    generated = await self.drafting.generate(
                        str(run["draft_id"]),
                        ArbitrationGenerateRequest(),
                        current_user,
                        pleading_plan=plan,
                        workflow_effect_key=f"workflow:{run_id}:generate:{run['plan_hash']}",
                    )
                    version = await self.db.arbitration_draft_versions.find_one(
                        {
                            "_id": generated.get("_workflow_version_id"),
                            "draft_id": str(run["draft_id"]),
                        }
                    )
                    if not version:
                        raise HTTPException(
                            status_code=409,
                            detail="Draft generation did not return its workflow-owned immutable version",
                        )
                    version_hash = version.get("version_hash") or immutable_version_hash(version)
                    if generated.get("_workflow_version_hash") != version_hash:
                        raise HTTPException(
                            status_code=409,
                            detail="Workflow draft generation returned a mismatched immutable hash",
                        )
                    await self.repository.complete_effect(
                        effect_key,
                        {"draft_version_id": version.get("_id"), "draft_version_hash": version_hash},
                        lease_token=effect_lease_token,
                    )
                except Exception as exc:
                    await self.repository.fail_effect(
                        effect_key,
                        error_code=type(exc).__name__,
                        lease_token=effect_lease_token,
                    )
                    raise
            version, validation, remediation_refs = await self._validate_with_bounded_remediation(
                run, version, current_user
            )
            version_hash = version.get("version_hash") or immutable_version_hash(version)
            status_value, node, next_action, progress = "awaiting_legal_review", "legal_review_gate", "legal_review", 85
            if validation.get("status") != "passed":
                next_action = "revise_draft"
            transition_update.update(
                {
                    "draft_version_id": version.get("_id"), "draft_version_hash": version_hash,
                    "authoritative_effects": [*(run.get("authoritative_effects") or []), "draft_version"],
                    "artifact_authors": {**(run.get("artifact_authors") or {}), "draft_version_hash": version.get("created_by")},
                    "validation_status": validation.get("status"),
                    "validation_blockers": validation.get("blockers") or [],
                    "validation_warnings": validation.get("warnings") or [],
                    "validation_artifact_set_id": validation["validation_artifact_set_id"],
                    "validation_artifact_set_hash": validation["validation_artifact_set_hash"],
                    "validation_report_id": validation["validation_report_id"],
                    "validation_report_hash": validation["report_hash"],
                    "validation_route": validation.get("route"),
                    "remediation_cycle": validation.get("remediation_cycle", 0),
                    **remediation_refs,
                }
            )
        elif gate == "draft":
            await self.drafting.approve(str(run.get("draft_id")), current_user, workflow_run=run)
            transition_update["authoritative_effects"] = [*(run.get("authoritative_effects") or []), "draft_approval"]
        elif gate == "export":
            transition_update["authoritative_effects"] = [*(run.get("authoritative_effects") or []), "export_authorization"]
        updated = await self.repository.transition(
            run_id,
            payload.state_version,
            {
                "status": status_value,
                "current_node": node,
                "next_action": next_action,
                "progress": progress,
                "required_human_role": (
                    None
                    if status_value in {"running", "completed"}
                    else "drafter"
                    if next_action == "revise_draft"
                    else "legal_reviewer"
                    if node == "legal_review_gate"
                    else "senior_legal"
                ),
                f"{gate}_approval_receipt_id": receipt["_id"],
                **transition_update,
            },
            event=f"{gate}_approved",
        )
        await self.repository.commit_approval(str(receipt["_id"]), run_id)
        flag = {
            "document_selection": "documents_selected", "matrix_review": "matrices_approved",
            "readiness": "readiness_approved", "plan": "plan_approved", "legal_review": "legal_review_approved",
            "draft": "draft_approved", "export": "export_authorized",
        }[gate]
        await self._sync_langgraph_checkpoint(updated, {flag: True})
        await observability_registry.record_arbitration_workflow(
            engine=str(updated.get("engine")), status=str(updated.get("status")), node=str(updated.get("current_node")), event=f"{gate}_approved"
        )
        paused_at = run.get("updated_at")
        if isinstance(paused_at, datetime):
            if paused_at.tzinfo is None:
                paused_at = paused_at.replace(tzinfo=timezone.utc)
            await observability_registry.record_arbitration_runtime_value(
                signal="human_wait_seconds", scope=gate,
                value=max(0.0, (datetime.now(timezone.utc) - paused_at).total_seconds()),
            )
        await self._record_shadow_safely(updated)
        return self.public_state(updated)

    async def resume(self, case_id: str, run_id: str, payload: Any, current_user: Any) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        self._assert_state_version(run, payload.state_version)
        if str(run.get("status")) in TERMINAL_WORKFLOW_STATUSES:
            raise HTTPException(status_code=409, detail="Terminal arbitration workflows cannot be resumed")
        current_node = str(run.get("current_node") or "")
        expected_gate = RESUMABLE_GATES.get(current_node)
        if not expected_gate:
            raise HTTPException(status_code=409, detail="Workflow is not waiting at a resumable human gate")
        await observability_registry.record_arbitration_runtime_event(
            signal="resume", node=current_node, reason=str(payload.gate or "unknown")
        )
        if payload.gate != expected_gate:
            raise HTTPException(status_code=409, detail="Resume gate does not match the paused workflow node")
        if payload.selected_document_ids and current_node != "document_selection_gate":
            raise HTTPException(status_code=422, detail="Documents can only be selected at the document selection gate")
        if (payload.answers or payload.directions) and current_node != "material_question_gate":
            raise HTTPException(status_code=422, detail="User directions can only be supplied at the material question gate")
        if current_node == "document_selection_gate" and not payload.selected_document_ids:
            raise HTTPException(status_code=422, detail="At least one scoped document is required to continue")
        if current_node == "matrix_review_gate" and payload.decision != "refresh":
            raise HTTPException(status_code=422, detail="Matrix review resume only supports decision=refresh")
        if current_node == "legal_review_gate" and payload.decision != "refresh_candidate":
            raise HTTPException(status_code=422, detail="Legal review resume only supports decision=refresh_candidate")
        if run.get("engine") == "langgraph_v1" and current_node in {
            "matrix_review_gate",
            "legal_review_gate",
        }:
            # Refreshes are authoritative graph commands, not service-side
            # replicas of analysis or validation. The engine records immutable
            # inputs, node effects and CAS transitions before checkpoint sync.
            from .langgraph_engine import LangGraphArbitrationEngine

            engine = LangGraphArbitrationEngine(self.db)
            if current_node == "matrix_review_gate":
                updated = await engine.refresh_analysis(
                    run, actor_id=_actor_id(current_user)
                )
            else:
                updated = await engine.refresh_validation(
                    run, actor_id=_actor_id(current_user)
                )
            await observability_registry.record_arbitration_workflow(
                engine=str(updated.get("engine")),
                status=str(updated.get("status")),
                node=str(updated.get("current_node")),
                event="resumed",
            )
            await self._record_shadow_safely(updated)
            return self.public_state(updated)
        update: Dict[str, Any] = {"status": "running", "next_action": "poll", "required_human_role": None}
        if current_node == "material_question_gate":
            required = {str(item.get("question_id")) for item in run.get("targeted_questions") or [] if item.get("required")}
            submitted = {str(key) for key in (payload.answers or {})}
            supplied = {str(key) for key, value in (payload.answers or {}).items() if str(value).strip()}
            known = {str(item.get("question_id")) for item in run.get("targeted_questions") or []}
            unknown = submitted - known
            if unknown:
                raise HTTPException(status_code=422, detail={"unknown_question_ids": sorted(unknown)})
            if required - supplied:
                raise HTTPException(status_code=422, detail={"missing_required_question_ids": sorted(required - supplied)})
            answer_snapshot = await self.repository.create_snapshot(
                run_id=run_id,
                kind="user_direction",
                payload={"answers": payload.answers, "directions": payload.directions, "question_snapshot_hash": run.get("question_snapshot_hash")},
                effect_key=f"{run_id}:snapshot:user-direction:{payload.state_version}",
            )
            update.update(
                {
                    "user_direction_snapshot_id": answer_snapshot["_id"],
                    "user_direction_snapshot_hash": answer_snapshot["snapshot_hash"],
                    "status": "awaiting_matrix_review", "current_node": "matrix_review_gate", "next_action": "review_matrices",
                    "required_human_role": "legal_reviewer", "progress": 40,
                    "last_material_editor_id": _actor_id(current_user),
                }
            )
        elif current_node == "matrix_review_gate":
            analysis = await self.domain.analyze(run, parallel=True)
            case = await self.cases.get_case(case_id)
            readiness = await self.cases._readiness_artifact_state(case, str(run.get("pleading_type")))
            update.update(
                {
                    "status": "awaiting_matrix_review", "current_node": "matrix_review_gate", "next_action": "review_matrices",
                    "required_human_role": "legal_reviewer", "progress": 40,
                    "analysis_artifact_set_id": analysis["analysis_artifact_set_id"],
                    "analysis_artifact_set_hash": analysis["analysis_artifact_set_hash"],
                    "matrix_revision_set_id": analysis["revision_set_id"], "matrix_revision_hash": analysis["revision_hash"],
                    "readiness_artifact_id": readiness["matrix_revision_set_id"], "readiness_artifact_hash": readiness["artifact_hash"],
                    "blockers": analysis["blockers"], "last_material_editor_id": _actor_id(current_user),
                }
            )
        elif current_node == "legal_review_gate":
            if not run.get("draft_id"):
                raise HTTPException(status_code=409, detail="Workflow has no case-linked draft")
            candidate = await self.drafting.repo.latest_version(str(run["draft_id"]))
            if not candidate:
                raise HTTPException(status_code=409, detail="No immutable draft candidate is available for validation")
            candidate, validation, remediation_refs = await self._validate_with_bounded_remediation(
                run, candidate, current_user
            )
            candidate_hash = candidate.get("version_hash") or immutable_version_hash(candidate)
            update.update(
                {
                    "status": "awaiting_legal_review",
                    "current_node": "legal_review_gate",
                    "next_action": "legal_review" if validation.get("status") == "passed" else "revise_draft",
                    "required_human_role": "legal_reviewer" if validation.get("status") == "passed" else "drafter",
                    "progress": 85,
                    "draft_version_id": candidate.get("_id"),
                    "draft_version_hash": candidate_hash,
                    "artifact_authors": {
                        **(run.get("artifact_authors") or {}),
                        "draft_version_hash": candidate.get("created_by"),
                    },
                    "validation_status": validation.get("status"),
                    "validation_blockers": validation.get("blockers") or [],
                    "validation_warnings": validation.get("warnings") or [],
                    "validation_artifact_set_id": validation["validation_artifact_set_id"],
                    "validation_artifact_set_hash": validation["validation_artifact_set_hash"],
                    "validation_report_id": validation["validation_report_id"],
                    "validation_report_hash": validation["report_hash"],
                    "validation_route": validation.get("route"),
                    "remediation_cycle": validation.get("remediation_cycle", 0),
                    "last_material_editor_id": candidate.get("created_by") or run.get("last_material_editor_id"),
                    **remediation_refs,
                }
            )
        if payload.selected_document_ids:
            case = await self.cases.get_case(case_id)
            manifest = []
            for document_id in sorted(set(payload.selected_document_ids)):
                record = await self.db.documents.find_one(
                    {"_id": document_id, "organization_id": case.get("organization_id"), "project_id": case.get("project_id")}
                )
                if not record:
                    raise HTTPException(status_code=400, detail=f"Selected document is outside case scope: {document_id}")
                manifest.append({"document_id": document_id, "version_id": record.get("current_version_id"), "sha256": record.get("sha256")})
            snapshot = await self.repository.create_snapshot(
                run_id=run_id,
                kind="document_manifest",
                payload={"documents": manifest},
                effect_key=f"{run_id}:snapshot:documents:{payload.state_version}",
            )
            if run.get("engine") == "langgraph_v1":
                update.update(
                    {
                        "documents_selected": True,
                        "document_manifest_id": snapshot["_id"],
                        "document_manifest_hash": snapshot["snapshot_hash"],
                        "status": "running",
                        "current_node": "document_selection_gate",
                        "next_action": "poll",
                        "required_human_role": None,
                        "progress": 25,
                        "last_material_editor_id": _actor_id(current_user),
                    }
                )
            else:
                analysis = await self.domain.analyze(run, parallel=True)
                case = await self.cases.get_case(case_id)
                readiness = await self.cases._readiness_artifact_state(case, str(run.get("pleading_type")))
                questions = self.domain.material_questions(analysis["blockers"], str(run.get("pleading_type")))
                question_snapshot = await self.repository.create_snapshot(
                    run_id=run_id, kind="material_questions", payload={"questions": questions},
                    effect_key=f"{run_id}:snapshot:questions:{analysis['revision_hash']}",
                )
                update.update({
                    "documents_selected": True, "document_manifest_id": snapshot["_id"], "document_manifest_hash": snapshot["snapshot_hash"],
                    "analysis_artifact_set_id": analysis["analysis_artifact_set_id"],
                    "analysis_artifact_set_hash": analysis["analysis_artifact_set_hash"],
                    "matrix_revision_set_id": analysis["revision_set_id"], "matrix_revision_hash": analysis["revision_hash"],
                    "readiness_artifact_id": readiness["matrix_revision_set_id"], "readiness_artifact_hash": readiness["artifact_hash"],
                    "status": "awaiting_user_direction" if questions else "awaiting_matrix_review",
                    "current_node": "material_question_gate" if questions else "matrix_review_gate",
                    "next_action": "answer_questions" if questions else "review_matrices",
                    "required_human_role": "legal_reviewer", "progress": 40, "blockers": analysis["blockers"],
                    "last_material_editor_id": _actor_id(current_user),
                    "targeted_questions": questions, "material_questions_required": bool(questions),
                    "question_snapshot_id": question_snapshot["_id"], "question_snapshot_hash": question_snapshot["snapshot_hash"],
                })
        updated = await self.repository.transition(run_id, payload.state_version, update, event="workflow_resumed")
        if current_node in {"document_selection_gate", "material_question_gate"}:
            await self._sync_langgraph_checkpoint(
                updated,
                {
                    "documents_selected": bool(updated.get("documents_selected")),
                    "user_direction_complete": str(updated.get("current_node")) != "material_question_gate",
                },
            )
        elif current_node == "legal_review_gate":
            await self._sync_langgraph_checkpoint(
                updated,
                {
                    "draft_generated": True,
                    "validation_complete": True,
                    "validation_artifact_set_id": str(updated.get("validation_artifact_set_id") or ""),
                    "validation_artifact_set_hash": str(updated.get("validation_artifact_set_hash") or ""),
                    "validation_status": str(updated.get("validation_status") or ""),
                    "validation_route": str(updated.get("validation_route") or ""),
                    "remediation_cycle": int(updated.get("remediation_cycle") or 0),
                },
                traverse=False,
            )
        await observability_registry.record_arbitration_workflow(
            engine=str(updated.get("engine")), status=str(updated.get("status")), node=str(updated.get("current_node")), event="resumed"
        )
        await self._record_shadow_safely(updated)
        return self.public_state(updated)

    async def cancel(self, case_id: str, run_id: str, payload: Any, current_user: Any) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        self._assert_state_version(run, payload.state_version)
        if str(run.get("status")) in TERMINAL_WORKFLOW_STATUSES:
            raise HTTPException(status_code=409, detail="Terminal arbitration workflows cannot be cancelled")
        updated = await self.repository.cancel_if_idle(
            run_id,
            payload.state_version,
            reason=payload.reason,
            actor_id=_actor_id(current_user),
        )
        await self._sync_langgraph_checkpoint(
            updated,
            {
                "cancellation_requested": True,
                "execution_status": "cancelled",
                "current_node": "cancelled",
                "next_action": "cancelled",
            },
            traverse=True,
        )
        await observability_registry.record_arbitration_workflow(
            engine=str(updated.get("engine")), status=str(updated.get("status")), node=str(updated.get("current_node")), event="cancelled"
        )
        return self.public_state(updated)

    async def checkpoints(self, run_id: str, *, limit: int = 50) -> list[Dict[str, Any]]:
        from .langgraph_engine import LangGraphArbitrationEngine

        return await LangGraphArbitrationEngine(self.db).list_checkpoints(run_id, limit=limit)

    async def fallback(self, case_id: str, run_id: str, payload: Any, current_user: Any) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        self._assert_state_version(run, payload.state_version)
        if run.get("engine") != "langgraph_v1":
            raise HTTPException(status_code=409, detail="Only LangGraph arbitration workflows can use the v2 fallback")
        if str(settings.ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE or "active").lower() != "active":
            raise HTTPException(
                status_code=409,
                detail="v2 fallback is no longer available for new writes under the configured compatibility mode",
            )
        protected_receipts = [
            run.get(f"{gate}_approval_receipt_id")
            for gate in ("legal_review", "draft", "export")
            if run.get(f"{gate}_approval_receipt_id")
        ]
        if run.get("authoritative_effects") or run.get("draft_version_id") or protected_receipts:
            raise HTTPException(
                status_code=409,
                detail="Fallback is prohibited after a LangGraph candidate, legal approval, draft approval, or export effect",
            )
        input_snapshot = await self.db.arbitration_workflow_snapshots.find_one(
            {"_id": run.get("input_snapshot_id"), "run_id": run_id, "kind": "input"}
        )
        if not input_snapshot or input_snapshot.get("snapshot_hash") != run.get("input_snapshot_hash"):
            raise HTTPException(status_code=409, detail="Immutable workflow input snapshot is missing or has drifted")
        fallback_refs = []
        for kind, id_field, hash_field in (
            ("input", "input_snapshot_id", "input_snapshot_hash"),
            ("document_manifest", "document_manifest_id", "document_manifest_hash"),
            ("evidence_manifest", "evidence_snapshot_id", "evidence_snapshot_hash"),
            ("opponent_pleading", "opponent_pleading_snapshot_id", "opponent_pleading_snapshot_hash"),
        ):
            if not run.get(id_field):
                continue
            snapshot = await self.db.arbitration_workflow_snapshots.find_one(
                {"_id": run.get(id_field), "run_id": run_id, "kind": kind}
            )
            if not snapshot or snapshot.get("snapshot_hash") != run.get(hash_field):
                raise HTTPException(status_code=409, detail=f"Immutable fallback {kind} snapshot is missing or drifted")
            fallback_refs.append(
                {
                    "kind": kind,
                    "snapshot_id": snapshot["_id"],
                    "snapshot_hash": snapshot["snapshot_hash"],
                }
            )
        if run.get("matrix_revision_hash"):
            current_matrix = await self.domain.inspect_matrix_revision(run)
            if current_matrix.get("revision_hash") != run.get("matrix_revision_hash"):
                raise HTTPException(
                    status_code=409,
                    detail="Fallback is prohibited after matrix drift; start a new v2 workflow from reviewed inputs",
                )
        fallback_binding = await self.repository.create_snapshot(
            run_id=run_id,
            kind="fallback_snapshot_binding",
            payload={
                "source_engine": "langgraph_v1",
                "target_engine": "arbitration_v2",
                "snapshots": fallback_refs,
                "matrix_revision_set_id": run.get("matrix_revision_set_id"),
                "matrix_revision_hash": run.get("matrix_revision_hash"),
            },
            effect_key=f"{run_id}:fallback-binding:{run.get('input_snapshot_hash')}",
        )
        updated = await self.repository.transition(
            run_id, payload.state_version,
            {
                "engine": "arbitration_v2",
                "engine_version": ArbitrationV2WorkflowEngine.version,
                "fallback_from_engine": "langgraph_v1",
                "fallback_input_snapshot_id": input_snapshot["_id"],
                "fallback_input_snapshot_hash": input_snapshot["snapshot_hash"],
                "fallback_snapshot_binding_id": fallback_binding["_id"],
                "fallback_snapshot_binding_hash": fallback_binding["snapshot_hash"],
                "fallback_reason": payload.reason,
                "fallback_available": False,
            },
            event="workflow_fallback_v2",
        )
        await observability_registry.record_arbitration_workflow(
            engine=str(updated.get("engine")), status=str(updated.get("status")), node=str(updated.get("current_node")), event="fallback_v2"
        )
        await observability_registry.record_arbitration_fallback(
            from_engine="langgraph_v1", reason="operator_requested"
        )
        return self.public_state(updated)

    async def _scoped_rollout_health(self, *, organization_id: str, project_id: str) -> Dict[str, Any]:
        runs = await _collect(
            self.db.arbitration_workflow_runs.find(
                {"organization_id": organization_id, "project_id": project_id}
            )
        )
        runs = sorted(
            runs,
            key=lambda row: str(row.get("created_at") or ""),
            reverse=True,
        )[: int(settings.ARBITRATION_ENGINE_ACCEPTANCE_WINDOW_RUNS)]
        run_ids = [str(run.get("_id")) for run in runs if run.get("_id")]
        acceptance_approvals = (
            await _collect(
                self.db.arbitration_workflow_approvals.find(
                    {
                        "run_id": {"$in": run_ids},
                        "gate": {"$in": ["readiness", "plan", "legal_review", "draft", "export"]},
                        "decision": "approved",
                        "receipt_status": "committed",
                    }
                )
            )
            if run_ids
            else []
        )
        for run in runs:
            run_id = str(run.get("_id") or "")
            run["_acceptance_receipts_valid"] = all(
                any(
                    str(receipt.get("run_id") or "") == run_id
                    and str(receipt.get("gate") or "") == gate
                    and not receipt.get("invalidated_at")
                    and str(receipt.get("artifact_hash") or "")
                    == str(run.get(GATE_ARTIFACT_FIELDS[gate]) or "")
                    for receipt in acceptance_approvals
                )
                for gate in ("readiness", "plan", "legal_review", "draft", "export")
            )
        events = (
            await _collect(self.db.arbitration_workflow_events.find({"run_id": {"$in": run_ids}}))
            if run_ids
            else []
        )
        health = build_rollout_health(runs, events)
        from .filing_export_queue import get_filing_export_queue

        queue_health = await get_filing_export_queue().health()
        health["filing_export_queue"] = queue_health
        if not queue_health.get("ready"):
            cutover = health.setdefault("primary_cutover", {})
            blockers = list(cutover.get("blockers") or [])
            if "filing_export_queue_not_ready" not in blockers:
                blockers.append("filing_export_queue_not_ready")
            cutover["blockers"] = blockers
            cutover["eligible"] = False
        receipt_id = str(settings.ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID or "").strip()
        receipt_hash = str(settings.ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256 or "").strip().lower()
        receipt = (
            await self.db.arbitration_production_acceptance_receipts.find_one({"_id": receipt_id})
            if receipt_id
            else None
        )
        receipt_valid = verify_acceptance_receipt(
            receipt,
            receipt_id=receipt_id,
            receipt_hash=receipt_hash,
            organization_id=organization_id,
            project_id=project_id,
        )
        health["acceptance_receipt"] = {
            "configured": bool(receipt_id and receipt_hash),
            "resolved": bool(receipt),
            "valid_for_scope": receipt_valid,
        }
        if not receipt_valid:
            cutover = health.setdefault("primary_cutover", {})
            blockers = list(cutover.get("blockers") or [])
            if "formal_acceptance_receipt_unresolved" not in blockers:
                blockers.append("formal_acceptance_receipt_unresolved")
            cutover["blockers"] = blockers
            cutover["eligible"] = False
        return health

    async def create_acceptance_receipt(
        self,
        case_id: str,
        payload: ArbitrationProductionAcceptanceRequest,
        current_user: Any,
    ) -> Dict[str, Any]:
        case = await self.cases.get_case(case_id)
        organization_id = str(case.get("organization_id") or "")
        project_id = str(case.get("project_id") or "")
        if organization_id not in set(payload.organization_ids) or (
            payload.project_ids and project_id not in set(payload.project_ids)
        ):
            raise HTTPException(status_code=409, detail="Acceptance receipt scope must include the authorized case scope")
        expires_at = payload.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="Acceptance receipt expiry must be in the future")
        health = await self._scoped_rollout_health(organization_id=organization_id, project_id=project_id)
        if health.get("status") != "ready" or not (health.get("filing_export_queue") or {}).get("ready"):
            raise HTTPException(
                status_code=409,
                detail={"message": "Operational acceptance evidence is incomplete", "health": health},
            )
        coverage = (health.get("primary_cutover") or {}).get("pleading_type_counts") or {}
        if any(int(coverage.get(kind) or 0) < 1 for kind in ("statement_of_claim", "statement_of_defence", "counterclaim", "rejoinder")):
            raise HTTPException(status_code=409, detail="All four pleading types require accepted production-like samples")
        resolved_acceptance = await resolve_server_backed_acceptance(
            self.db,
            criteria=dict(payload.criteria),
            evidence_hashes=dict(payload.evidence_hashes),
            stakeholder_signoff_ids=list(payload.stakeholder_signoffs),
            organization_id=organization_id,
            project_id=project_id,
        )
        if not resolved_acceptance.get("valid"):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Production acceptance requires server-backed real-execution evidence and bound stakeholder signoffs",
                    "reason": resolved_acceptance.get("reason"),
                    "missing_criteria": resolved_acceptance.get("missing_criteria") or [],
                },
            )
        receipt = {
            "_id": str(uuid.uuid4()),
            "status": "accepted",
            "criteria": dict(payload.criteria),
            "evidence_hashes": {key: str(value).lower() for key, value in payload.evidence_hashes.items()},
            "stakeholder_signoffs": sorted(set(payload.stakeholder_signoffs)),
            "acceptance_bundle_hash": resolved_acceptance["bundle_hash"],
            "evidence_record_ids": resolved_acceptance["evidence_record_ids"],
            "signoff_receipt_ids": resolved_acceptance["signoff_receipt_ids"],
            "signoff_actor_ids": resolved_acceptance["signoff_actor_ids"],
            "signoff_roles": resolved_acceptance["signoff_roles"],
            "organization_ids": sorted(set(payload.organization_ids)),
            "project_ids": sorted(set(payload.project_ids)),
            "health_snapshot_hash": artifact_hash(health),
            "notes": payload.notes,
            "accepted_by": _actor_id(current_user),
            "accepted_at": datetime.now(timezone.utc),
            "expires_at": expires_at,
        }
        receipt["receipt_hash"] = acceptance_hash(receipt)
        receipt["server_signature"] = sign_acceptance(receipt["receipt_hash"])
        await self.db.arbitration_production_acceptance_receipts.insert_one(receipt)
        return receipt

    async def operations_health(self, case_id: str) -> Dict[str, Any]:
        case = await self.cases.get_case(case_id)
        health = await self._scoped_rollout_health(
            organization_id=str(case.get("organization_id") or ""),
            project_id=str(case.get("project_id") or ""),
        )
        await observability_registry.record_arbitration_workflow_health(health)
        return health

    @staticmethod
    def public_state(run: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: run.get(key)
            for key in (
                "_id", "case_id", "draft_id", "pleading_type", "engine", "rollout_mode", "status",
                "rollout_policy_version", "rollout_decision_reason", "rollout_decision_hash",
                "acceptance_receipt_sha256", "v2_compatibility_mode",
                "current_node", "next_action", "state_version", "graph_version", "state_schema_version",
                "progress", "blockers", "required_human_role", "fallback_available", "last_checkpoint_at",
                "checkpoint_sync_status", "checkpoint_sync_state_version",
                "created_at", "updated_at", "document_manifest_hash", "opponent_pleading_snapshot_hash",
                "evidence_snapshot_hash", "analysis_artifact_set_id", "analysis_artifact_set_hash",
                "matrix_revision_set_id", "matrix_revision_hash",
                "readiness_artifact_hash", "plan_id", "plan_hash", "draft_version_id", "draft_version_hash",
                "validation_status",
                "validation_artifact_set_id", "validation_artifact_set_hash",
                "validation_report_id", "validation_report_hash", "validation_route",
                "remediation_artifact_id", "remediation_artifact_hash",
                "filing_export_id", "filing_export_effect_key",
                "targeted_questions",
                "fallback_reason", "fallback_from_engine", "fallback_input_snapshot_id", "fallback_input_snapshot_hash",
                "fallback_snapshot_binding_id", "fallback_snapshot_binding_hash",
            )
        } | {
            "run_id": run.get("_id"),
            "blockers": list(run.get("blockers") or []),
            "validation_blockers": list(run.get("validation_blockers") or []),
            "validation_warnings": list(run.get("validation_warnings") or []),
            "remediation_cycle": int(run.get("remediation_cycle") or 0),
            "approval_receipt_ids": {
                gate: run.get(f"{gate}_approval_receipt_id")
                for gate in GATE_ARTIFACT_FIELDS if run.get(f"{gate}_approval_receipt_id")
            },
        }
