# Claim → Arbitration Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the skill engine ready to serve claims and arbitration, put a real legal gate on skill publication, and fix the claim register's most damaging gaps, with no change to any AI behaviour users already have.

**Architecture:** The four new packs ship in the bundle as drafts and stay invisible to drafters until a version is approved and published. Approval is a separate record bound to the version, content hash and evaluation result; `SkillRegistry.publish()` refuses without it. Claims gain a status transition table and a deterministic notice assessment that never presents an assumed period as contractual.

**Tech Stack:** FastAPI, Pydantic v2, Motor/MongoDB, React 18 + Vite + TypeScript, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-15-claim-arbitration-skills-audit-and-plan.md` (Move 0, Phase 0 row, decisions D-01 and D-03, correction B).

## Global Constraints

- Authorization only through `PolicyService.authorize` / `require_permission` / `build_scope_query`; permission names in `dms.*` or the existing `drafting.skill.*` family.
- Routers re-raise `(BaseDomainError, HTTPException)` together; mutating routes carry `@handle_exceptions`.
- New rate limiters pass `scope=`.
- Migration documentary notes go in `notices=`, as literals at the call site.
- Regenerate the route inventory after any route change (`scripts/rbac_phase0_route_inventory.py`).
- The four new packs are installed as **draft**. Nothing in this phase publishes them.
- No change to arbitration engine selection, rollout flags or approval gates.
- Published letter packs (`1.0.0`) keep working unchanged.

## Deferred from the spec's Move 0, with reason

- `SkillStep` generalisation (`subject_type`, `skill_steps` collection) and the prompt-provider interface: they have no consumer until the Claim Bench (Phase 1) and are built with it.
- Output schemas beyond one per pack, and the new validators: each arrives with the step that produces it.
- Notice period extraction from contract instruments (`NoticeRequirement`): Phase 1 (Move 2). Phase 0 only labels today's figure honestly as assumed.

## Review Focus

1. A bundled draft pack must never resolve for drafting, even with empty skill collections (the registry falls back to the bundle).
2. An approval given for one content hash must not unlock a re-imported draft with different text under the same version number.
3. A Super Admin with no reviewer role must be refused when recording a legal approval, and when publishing a version that requires one.
4. One user must not be able to act as evaluator, legal signatory and publisher of the same version.
5. A claim whose notice was served late against an *assumed* window must say "assumed", not "late under the contract".

---

### Task 1: Schema registry and configurable model

**Files:** Create `services/skills/schemas.py`; modify `models/letter_bench.py`, `services/skills/executor.py`, `core/config.py`; test `tests/test_skill_registry_router_executor.py`.

**Interfaces — Produces:** `register_schema(key: str, model: type[BaseModel]) -> None`, `get_schema(key: str) -> Optional[type[BaseModel]]`, `registered_schema_keys() -> list[str]`; `settings.SKILL_MODEL` (default `"gpt-4o"`). `models.letter_bench.OUTPUT_SCHEMAS` stays importable.

- [ ] Test: registering a key twice with a different model raises `ValueError`; the three letter schemas resolve through `get_schema`; executor still refuses an unknown key.
- [ ] Implement; executor reads the model name from settings.
- [ ] Run the two skill test modules; commit.

### Task 2: Install the four packs as bundled drafts

**Files:** Create `initial_data/skills/{indian-infra-claims,indian-infra-eot-claims,indian-infra-arbitration-pleadings,indian-infra-arbitration-hearing}/`; create `models/claim_skill_outputs.py`; modify `models/skill.py`, `services/skills/system_skills.py`, `services/skills/registry.py`; tests in both skill test modules.

**Interfaces — Produces:** `SkillApprovalRequirement(kind: Literal["technical","legal"], reviewer_role: str)`; `SkillManifest.required_approvals: list[SkillApprovalRequirement]`; constants `PLEADINGS_SKILL_KEY`, `HEARING_SKILL_KEY`; `PENDING_APPROVAL_SKILLS: dict[str, str]` (replaces `KNOWN_UNINSTALLED_SKILLS`, which stays as an alias); schema keys `claim_head_test.v1`, `delay_event_classification.v1`, `pleading_head.v1`, `hearing_deliverable.v1`. Unavailable reason for a draft-only pack: `"Awaiting legal approval"`.

- [ ] Tests: seven packs load and hash; the four new ones have status `draft` and the required approvals from the spec table; `resolve()` returns no version for them on an empty DB; `list_effective` lists claims and EOT as unavailable with the new reason; pleadings and hearing drafts are not listed to drafters.
- [ ] Implement; run; commit.

### Task 3: Publication gate

**Files:** Create `services/skills/approvals.py`; modify `models/skill.py`, `services/skills/registry.py`, `routers/skills.py`, `core/permissions.py`; create `tests/test_skill_publication_gate.py`.

**Interfaces — Produces:**
- `SkillApproval` (fields per spec Move 0), collection `skill_approvals`.
- `eval_report_hash(report: SkillEvalReport) -> str` over suite, score, threshold, case_count.
- `SkillEvalReport.recorded_by: Optional[str]`.
- `SkillApprovalService(db).record(skill_key, version, kind, reviewer_role, eval_report, actor) -> SkillApproval`, `.revoke(approval_id, actor)`, `.list(skill_key, version)`, `.status(version, eval_report) -> list[ApprovalStatus]`.
- `SkillRegistry.publish(...)` gains the gate; signature unchanged.
- Routes: `GET/POST /skills/{key}/versions/{version}/approvals`, `POST /skills/approvals/{id}/revoke`. Permission `drafting.skill.approve`.
- Role claims via the existing `actor_roles()` from `arbitration_drafting/approval_policy.py`.

- [ ] Tests (each Review Focus item 2–4 plus): publish refused with no approvals and the detail lists what is missing; refused when an approval's content hash or eval hash differs; refused after revoke; refused when publisher is the legal signatory or the evaluator; approval refused for a user without the role, including a superadmin; succeeds with the full set; a manifest with no `required_approvals` publishes as before.
- [ ] Implement; run; regenerate route inventory; commit.

### Task 4: Seed migration

**Files:** Create `migrations/v20261115_0001_seed_claim_arbitration_skill_drafts.py`; test `tests/test_skill_publication_gate.py`.

- [ ] Test: dry run writes nothing; apply inserts four `draft` versions and no `skills.latest_published_version`; re-run is a no-op; a stored version with a different hash is a warning; the unsigned-letter-packs note is a notice.
- [ ] Implement; run with `test_migration_warning_classification.py`; commit.

### Task 5: Project working perspective

**Files:** Modify `models/project.py`, `routers/projects.py` (only if update payloads whitelist fields), client project form; tests alongside existing project tests.

**Interfaces — Produces:** `Project.contractual_capacity: Optional[Literal["contractor","engineer","employer"]] = None`.

- [ ] Test: field round-trips; an invalid value is 422; absent means `None`, never a default.
- [ ] Implement; commit.

### Task 6: Claim status guard and notice assessment

**Files:** Create `services/claims_notice.py`; modify `models/claim.py`, `routers/claims.py`, `services/sla_service.py`; tests `tests/test_claims.py`, new `tests/test_claim_notice.py`.

**Interfaces — Produces:**
- `ALLOWED_STATUS_TRANSITIONS: dict[ClaimStatus, set[ClaimStatus]]`; `POST /claims/{id}/status` returns 409 with the allowed targets on an illegal move.
- `ClaimBase.awareness_date: Optional[datetime]`.
- `assess_notice(claim: dict, *, now, windows) -> Optional[NoticeAssessment]` with `basis_date`, `basis` (`awareness`|`event`), `period_days`, `period_source` (`organisation_rule`|`assumed_default`), `due_date`, `days_remaining`, `served_on`, `served_late_by_days`, `state` (`open`|`due_soon`|`overdue`|`served_in_time`|`served_late`). Returned on claim reads as `notice_assessment`.

- [ ] Tests: event 2 Mar, notice 11 Apr, type eot → `served_late`, 12 days, `period_source == "assumed_default"`; awareness date takes precedence over event date; no basis date → `None`; an organisation rule yields `organisation_rule`; illegal transition `closed → draft` is 409; legal `draft → notified` passes.
- [ ] Implement; run; regenerate route inventory; commit.

### Task 7: Claims screens

**Files:** Create `client/src/lib/format-in.ts`; modify `ClaimsRegisterPage.tsx`, `ClaimDetailPage.tsx`, `ClaimApprovalDialog.tsx`, `claims-api.ts`, `claims-helpers.ts`, `Sidebar.tsx`; tests `lib/__tests__/format-in.test.ts`, `claims-helpers.test.ts`.

**Interfaces — Produces:** `formatINR(amount, currency?) -> string` (Indian grouping), `formatCrore(amount) -> string`, `formatDateIN(iso) -> "DD-MM-YYYY"`.

- [ ] Tests: `formatINR(184500000)` → `₹18,45,00,000`; `formatCrore(184500000)` → `₹18.45 Cr`; `formatDateIN("2026-11-20")` → `20-11-2026`; notice label for assumed-late reads "Notice served 12 days after an assumed 28-day period".
- [ ] Implement: dialog captures event, awareness and notice dates, EOT days, clauses, currency; notice column and detail banner; status menu offers only allowed targets and shows the server message on refusal; labelled row actions; readable badges; reviewer picked from users; "Registration" typo.
- [ ] `npm run lint`, vitest for touched files, screenshot check; commit.

### Task 8: Skills admin

**Files:** Move `components/letter-bench/Skill{Rail,StepDrawer,Palette}.tsx` to `components/skills/` (re-export from the old paths); modify `routes.tsx`, `SkillAdminPage.tsx`, skills API client.

- [ ] Implement: route `/skills` with `/letters/skills` redirecting; per version, required approvals with who/when/what bound to, and the reason Publish is disabled.
- [ ] Lint, existing Letter Bench e2e (mocked), screenshot check; commit.
