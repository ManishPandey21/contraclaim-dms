# Contract Master Enhancement — Implementation Plan

**Module:** Contract Controls → Contract Master
**Goal:** Introduce one authoritative per-contract record of value + dates + BG-validity rules, and wire the existing **Key Date**, **Variation**, and **Bank Guarantee** registers to read from it — so the *BG required-up-to date recomputes automatically when EOT is granted*, the *variation percentage is accurate*, and *project start dates stop being re-typed per milestone*.

> Read-only planning pass. No application code changed. Follows the established conventions (Motor/MongoDB + Pydantic aliased to `_id`, `PolicyService` authz + `build_scope_query`, `AuditEventService`, the route-inventory CI guard, the register/service/router pattern used by Claims/Key-Date/Variation/BG).

---

## 1. Repo reality check (why this is needed)

| Concern | Today | Consequence |
|---|---|---|
| Contract value / completion dates | **Not stored anywhere.** `models/project.py` has no start/end/completion/value/DLP fields (and `ConfigDict` has no `extra="allow"`); `routers/projects.py` never sets them. The 37 `start_date` refs are all in subscriptions / reports / scope — unrelated. | Each register re-enters the same data. |
| First-class contract entity | **None.** Contracts are document upload records (`contract_models.py`); `contract_id` is a free string on Variation/BG. | No place to hang contract-level facts. |
| Variation `original_contract_value` | Entered **per variation** | Percentage/revised value depend on the user typing the same number every row. |
| BG `contractual_required_up_to` | Entered **per BG**, editable | Cannot auto-move when EOT is granted; the PDF's core requirement is unmet (currently an editable field only). |
| Key-Date `project_start_date` | Passed **per milestone** (fallback) because `db.projects` carries no start date | Re-typed each time. |

**Decision:** create a dedicated **`contract_master`** collection keyed by `(organization_id, project_id, contract_id)` — **not** extend the `Project` model (owned by a separate router; extending it would entangle project CRUD and RBAC). A new entity gives clean RBAC/audit, supports multiple contract packages per project, and matches the PDF's project-vs-contract distinction. Default usage = one contract master per project (`contract_id` optional, defaults to a single "primary" key).

---

## 2. Data model — `contract_master`

`models/contract_master.py` (Pydantic, `alias="_id"`, `populate_by_name=True`):

| Field | Notes |
|---|---|
| `id` (`_id`), `organization_id`, `project_id` | tenant scope |
| `contract_id` | package key (default `"primary"`); unique per (org, project, contract_id) |
| `contract_name`, `contract_code` | |
| `client_name`, `contractor_name`, `engineer_name` | party names (display only; not linked to the parties module in v1) |
| `currency` (default INR) | |
| `original_contract_value` | baseline; **never overwritten** |
| `current_contract_value` | = original + cumulative approved variation (synced from Variation summary) |
| `contract_start_date` | drives Key-Date calculation |
| `original_completion_date` | baseline completion; never overwritten |
| `revised_completion_date` | moves on EOT; nullable (falls back to original) |
| `defect_liability_period_days` | e.g. 365 |
| `reporting_period` | string (e.g. "monthly") |
| `bg_validity_rules` | `dict[bg_type → {basis, offset_days}]` where `basis ∈ {completion, dlp_end}`; a `default` entry covers unspecified types |
| `created_by/at`, `updated_by/at` | audit |

**Derived (pure helpers in the service):**
- `effective_completion_date(cm)` = `revised_completion_date or original_completion_date`.
- `dlp_end_date(cm)` = `effective_completion_date + defect_liability_period_days`.
- `bg_required_up_to(cm, bg_type)` = base (`effective_completion_date` or `dlp_end_date` per the rule's `basis`) + `offset_days`. This is the formula the PDF asks to be "configurable contract-wise per BG type".

Indexes: unique `(organization_id, project_id, contract_id)`; `(organization_id, project_id)`.

---

## 3. API — `routers/contract_master.py` (`/api/contracts/master`)

| Endpoint | Permission | Purpose |
|---|---|---|
| `GET /contracts/master?project_id=&contract_id=` | `dms.contract.master.view` | fetch the contract master(s) for a scope |
| `POST /contracts/master` | `dms.contract.master.manage` | create |
| `PUT /contracts/master/{id}` | `dms.contract.master.manage` | edit |
| `POST /contracts/master/{id}/revise-completion` | `dms.contract.master.manage` | set `revised_completion_date` (the EOT hook) → triggers BG recompute |
| `GET /contracts/master/{id}/bg-required-dates` | `dms.contract.master.view` | preview `bg_required_up_to` per BG type |

All `PolicyService`-gated, `build_scope_query`-scoped, `AuditEventService`-audited. New permission family `dms.contract.master.{view,manage}` (legacy-aliased to `projects:read` / `projects:update`).

---

## 4. Integrations (the payoff)

### 4a. Variation Register
- On `create`/`summary`, if `original_contract_value` is omitted, **default it from the contract master** for `(project_id, contract_id)`.
- After an approved variation changes, **sync** `contract_master.current_contract_value = original + cumulative_approved_variation` (reuse the existing `variation_summary` pure function). Lightweight: recompute on variation create/update/delete for that contract.

### 4b. Bank Guarantee Register — *the headline requirement*
- On BG `create`, if `contractual_required_up_to` is omitted, **compute it** = `bg_required_up_to(cm, bg_type)`.
- Add `BankGuaranteeService.recompute_required_dates(contract_scope)`: for every non-released BG under a contract, recompute `contractual_required_up_to` from the contract master and re-evaluate `extension_required`. Audited per BG that changes.
- **EOT linkage**: `POST /contracts/master/{id}/revise-completion` (and the optional Key-Date hook below) calls `recompute_required_dates` → BG required-up-to moves → `extension_required` and the 45/30-day alert scan pick it up automatically. This converts today's *manually editable* field into the *auto-driven* field the spec mandates, **without removing** the manual override (authorised users can still edit on extend).

### 4c. Key Date / Milestone Tracker
- `KeyDateService._project_start` already reads `db.projects` then falls back to the milestone payload. Extend it to also read `contract_master.contract_start_date` for `(project_id, contract_id)`. Removes the per-milestone re-entry; backward-compatible (payload override still wins).
- **Optional auto-EOT hook (CM-4):** when a Key-Date EOT approval moves the *completion* milestone, offer (or auto-apply, behind a flag) `revise-completion` on the contract master → cascades to BG. Kept optional because "which milestone = contract completion" is a project decision; default is the explicit `revise-completion` action.

---

## 5. Frontend

- `services/contract-master-api.ts` — typed client.
- `pages/ContractMasterPage.tsx` (route `/contracts/master`, SideBar under **Contract Controls**) — form for value/dates/DLP/parties + a `bg_validity_rules` editor (per-BG-type offset) + a "Revise completion (EOT)" action showing the resulting BG required-up-to preview.
- Wire the **Variation** and **BG** create forms to **auto-fill** `original_contract_value` / `contractual_required_up_to` from the selected project's contract master (with an editable override).
- Add the route to `ROUTE_PERMISSIONS` + SideBar **together** (route-inventory guard enforces it).
- Pure helper + unit test for `bg_required_up_to` mirrored on the client for the preview.

---

## 6. Dashboard

Extend the existing Variation/BG dashboard widgets to read `current_contract_value` and `% variation` straight from the contract master (single source), instead of recomputing client-side per page.

---

## 7. Tests

- `bg_required_up_to` per basis (completion vs dlp_end) + offset; `effective_completion_date` fallback.
- `recompute_required_dates` moves BG required-up-to and flips `extension_required` after `revise-completion`.
- Variation create defaults `original_contract_value` from the master; `current_contract_value` sync after approval.
- Key-date create reads `contract_start_date` from the master.
- Cross-tenant 403 on contract-master endpoints; audit emitted on revise-completion.
- Frontend: contract-master helper test; route-inventory guard covers the new route.

---

## 8. Phasing

| Phase | Scope | Acceptance |
|---|---|---|
| **CM-1** | `contract_master` model + service (CRUD + pure `bg_required_up_to`/completion helpers) + router + `dms.contract.master.*` perms + indexes + main wiring + tests | Contract master CRUD works, scoped/audited; formula tested |
| **CM-2** | Wire Variation (default + `current_contract_value` sync) and BG (compute required-up-to + `recompute_required_dates` + `revise-completion` cascade) + tests | Approving an EOT-style completion revision moves BG required-up-to and flips extension-required automatically |
| **CM-3** | Frontend ContractMasterPage + auto-fill in Variation/BG forms + SideBar/route/perms + helper test | Users manage the master and the registers auto-fill from it |
| **CM-4** *(optional)* | Auto Key-Date-EOT → revise-completion hook (flagged) | Granting EOT on the completion milestone cascades to BG without a manual step |

---

## 9. Risks & decisions

- **One-vs-many contracts per project:** modelled as many (`contract_id`), used as one by default — no migration risk, room to grow.
- **Don't overwrite baselines:** `original_contract_value` and `original_completion_date` are immutable; only `current_*`/`revised_*` move (same discipline as the BG extension history and Key-Date original date).
- **Manual override preserved:** auto-computed BG required-up-to never blocks an authorised manual edit on extend.
- **Backward compatible:** every auto-fill is a *default* — existing Variation/BG/Key-Date records and their per-record values keep working untouched.
- **Open decision (needs your call):** default `bg_validity_rules` per BG type — e.g. Performance/Additional-Performance → `dlp_end + 0`, Mobilisation/Plant Advance → `completion + 0`, Retention → `dlp_end + 0`. Confirm the offsets against your contract forms before CM-1.

**Recommended first step:** CM-1 (the entity + formula), since CM-2/3 depend on it and it's the piece that makes the BG auto-recompute and accurate variation % possible.
