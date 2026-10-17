# F-A9D-1 role-contract alignment - frozen production cutover diff (R-A9F)

**Frozen 2026-09-20 in release programme R-A9F. Not yet executed against production.**

This is the exact diff the cutover operation is expected to produce, sealed so that the
production run is a *comparison* rather than a discovery. It is not a plan written by hand:
every number and every permission name below is read out of the sealed R-A9E Stage B Segment A
evidence, where the operation ran three times against a **restored copy of the production
database** on an isolated staging stack.

| Field | Value |
|---|---|
| Operation | `python -m rbac_backend.scripts.align_role_contract` (module `rbac_backend/services/role_contract_alignment.py`) |
| Operation label in the report | `role_contract_alignment/R-A9D` - a label only; the F-A9D-1 removal contract arrived in `fe728b2` and the string was left alone so the certified candidate would not move |
| Certified on | candidate `fe728b205ed5dfb20d88040bc44214ca71d11c43` |
| Evidence | `/var/backups/contraclaim-stg-evidence/R-A9E-20260916T081258Z/candidate-fe728b2/` files `22`, `22a`, `22b`, `22c`, `22d` |
| Input state | production database copy restored into `contraclaim_staging` in Segment A |
| In the migration catalogue? | **No, deliberately.** `migrate_database` never runs it. See `PRODUCTION_CUTOVER_CHECKLIST.md` section 14 |
| Roles in `ALIGNED_ROLE_IDS` | `orgadmin`, `projectadmin` - and no others |
| `OWNER_APPROVED_REMOVALS` | `{"orgadmin": ["billing.plan.manage"], "projectadmin": ["billing.plan.manage"]}` |

## The classification contract

Every permission the role document holds falls into exactly one class, and all three are
reported:

* **Class A - ADD.** Permissions this release's `DEFAULT_ROLES` contract defines that the
  production document does not hold. Added.
* **Class B - REMOVE.** Only names listed in `OWNER_APPROVED_REMOVALS`. A removal that is not
  listed, or a removal of something inside the role's own release contract, is **refused**
  (`unapproved_removals`, exit 3) - it is never executed.
* **Class C - PRESERVE + REPORT.** Every other permission the document holds outside the
  release contract. Kept, and named in the report so it is visible rather than silently
  carried.

A Class B removal is a **revocation** and takes the fail-closed D4-B path: holders are read by
id *and* by legacy spelling, the authority change is announced before the write, holders are
re-read and the union invalidated after it, and if holders cannot be read or the announcement
cannot be made **the role is not changed at all**. Class A additions take the silent path -
they can only make a cached decision more restrictive.

## ROLE / BEFORE / ADD / REMOVE / PRESERVE / EXPECTED AFTER

| ROLE | BEFORE | ADD (class A) | REMOVE (class B) | PRESERVE (class C) | EXPECTED AFTER |
|---|---:|---:|---|---:|---:|
| `orgadmin` | 54 | 82 | `billing.plan.manage` (1) | 6 | **135** |
| `projectadmin` | 53 | 66 | `billing.plan.manage` (1) | 4 | **118** |
| `projectuser` | - | **0** | **none** | - | unchanged - outside `ALIGNED_ROLE_IDS` |
| `orguser` | - | **0** | **none** | - | unchanged - outside `ALIGNED_ROLE_IDS` |

Arithmetic, which the production run must reproduce exactly:

* `orgadmin`: 54 + 82 - 1 = **135**; release contract = 129, plus 6 preserved = 135 = 135.
* `projectadmin`: 53 + 66 - 1 = **118**; release contract = 114, plus 4 preserved = 118 = 118.

`unapproved_additions_total = 0`, `unapproved_removals_total = 0`, `warnings = []`.

## No accidental re-grant - measured, not asserted

Segment A read every other role document back after the apply (file `22`, section
"A2 WHAT CHANGED"):

| Role | Changed? | `dms.task.manage` | `dms.project.manage` | `billing.plan.manage` |
|---|---|---|---|---|
| `projectuser` | **unchanged** | no | **no** | no |
| `orguser` | **unchanged** | no | **no** | no |
| `contractmgr_org` | **unchanged** | no | yes (pre-existing) | yes (pre-existing, **kept**) |
| `settings_manager` | **unchanged** | yes (pre-existing) | yes (pre-existing) | yes (pre-existing, **kept**) |
| `limited_user` | **unchanged** | yes (pre-existing) | yes (pre-existing) | yes (pre-existing, **kept**) |

`ONLY APPROVED ROLE DOCS CHANGED: 0 of 10 other documents changed []`. `USERS UNCHANGED:
users=9 digest IDENTICAL` - the operation never writes `db.users`. The role **set** was
unchanged: nothing was created and nothing was reactivated.

The F-A9D-1 removal is deliberately **not** generalised: `contractmgr_org`,
`settings_manager` and `limited_user` keep their `billing.plan.manage`, because the owner
decision names two roles and the operation touches only what it was told to.

## Class C - preserved and reported

`orgadmin` (6): `billing.plan.view`, `drafting.final.view`, `drafting.request.create`, `drafting.request.view`, `drafting.review.approve`, `roles:assign`

`projectadmin` (4): `billing.plan.view`, `drafting.final.view`, `drafting.request.create`, `drafting.request.view`

## Class A - the exact additions

### `orgadmin` - 82 additions

| | | | |
|---|---|---|---|
| `concerns:create` | `concerns:delete` | `concerns:read` | `concerns:update` |
| `dms.admin` | `dms.ai.contract_processing.run` | `dms.arbitration.admin` | `dms.arbitration.approve` |
| `dms.arbitration.audit` | `dms.arbitration.create` | `dms.arbitration.edit` | `dms.arbitration.export` |
| `dms.arbitration.generate` | `dms.arbitration.view` | `dms.audit.view` | `dms.chronology.admin` |
| `dms.chronology.create` | `dms.chronology.edit` | `dms.chronology.export` | `dms.chronology.verify` |
| `dms.chronology.view` | `dms.claim.assess` | `dms.claim.create` | `dms.claim.delete` |
| `dms.claim.edit` | `dms.claim.manage` | `dms.claim.view` | `dms.contract.applicability.manage` |
| `dms.contract.appraisal.approve` | `dms.contract.appraisal.create_registers` | `dms.contract.appraisal.edit` | `dms.contract.appraisal.export` |
| `dms.contract.appraisal.generate` | `dms.contract.appraisal.reject` | `dms.contract.appraisal.view` | `dms.contract.catalogue.browse` |
| `dms.contract.clause.create` | `dms.contract.clause.read` | `dms.contract.read` | `dms.contract.timeline.view` |
| `dms.contract.update` | `dms.evidence_graph.manage` | `dms.evidence_graph.verify` | `dms.evidence_graph.view` |
| `dms.insurance.create` | `dms.insurance.delete` | `dms.insurance.edit` | `dms.insurance.export` |
| `dms.insurance.manage_types` | `dms.insurance.view` | `dms.keydate.baseline.freeze` | `dms.keydate.eot.determine` |
| `dms.keydate.eot.freeze_determination` | `dms.keydate.eot.lock_submission` | `dms.keydate.eot.supersede` | `dms.task.create` |
| `dms.task.delete` | `dms.task.edit` | `dms.task.manage` | `dms.task.view` |
| `organizations:create` | `organizations:delete` | `organizations:update` | `parties:create` |
| `parties:delete` | `parties:read` | `parties:update` | `permissions:read` |
| `representatives:create` | `representatives:delete` | `representatives:update` | `roles:create` |
| `roles:delete` | `roles:read` | `roles:update` | `tags:create` |
| `tags:delete` | `tags:read` | `tags:update` | `users:create` |
| `users:delete` | `users:update` |  |  |

### `projectadmin` - 66 additions

| | | | |
|---|---|---|---|
| `concerns:read` | `dms.admin` | `dms.ai.contract_processing.run` | `dms.arbitration.admin` |
| `dms.arbitration.approve` | `dms.arbitration.audit` | `dms.arbitration.create` | `dms.arbitration.edit` |
| `dms.arbitration.export` | `dms.arbitration.generate` | `dms.arbitration.view` | `dms.audit.view` |
| `dms.chronology.admin` | `dms.chronology.create` | `dms.chronology.edit` | `dms.chronology.export` |
| `dms.chronology.verify` | `dms.chronology.view` | `dms.claim.assess` | `dms.claim.create` |
| `dms.claim.delete` | `dms.claim.edit` | `dms.claim.manage` | `dms.contract.appraisal.create_registers` |
| `dms.contract.appraisal.edit` | `dms.contract.appraisal.export` | `dms.contract.appraisal.generate` | `dms.contract.appraisal.reject` |
| `dms.contract.clause.create` | `dms.contract.clause.read` | `dms.contract.read` | `dms.contract.timeline.view` |
| `dms.contract.update` | `dms.document.share` | `dms.evidence_graph.manage` | `dms.evidence_graph.verify` |
| `dms.evidence_graph.view` | `dms.insurance.create` | `dms.insurance.delete` | `dms.insurance.edit` |
| `dms.insurance.export` | `dms.insurance.manage_types` | `dms.insurance.view` | `dms.keydate.baseline.freeze` |
| `dms.keydate.eot.determine` | `dms.keydate.eot.freeze_determination` | `dms.keydate.eot.lock_submission` | `dms.keydate.eot.supersede` |
| `dms.keydate.eot_submit` | `dms.project.manage` | `dms.task.create` | `dms.task.delete` |
| `dms.task.edit` | `dms.task.manage` | `dms.task.view` | `parties:read` |
| `permissions:read` | `roles:create` | `roles:read` | `roles:update` |
| `tags:create` | `tags:delete` | `tags:read` | `tags:update` |
| `users:create` | `users:update` |  |  |

## The four-step operation, and what each step must report

The operation stays **inspect -> dry-run -> apply -> re-apply**, and the second apply must be
a NOOP. Measured in R-A9E Segment A:

| Step | Command | Required result | Measured in R-A9E |
|---|---|---|---|
| 1. INSPECT | `align_role_contract` | writes nothing; the diff equals this document | exit 0; `mode=inspect`; diff as above |
| 2. DRY-RUN | `align_role_contract` again | the plan is stable **and** still wrote nothing | exit 0; `DRY RUN WROTE NOTHING: users_digest_same=True roles_all_same=True` |
| 3. APPLY | `align_role_contract --apply` | the measured diff; `second_apply_is_noop: true` | exit 0; orgadmin 54->135, projectadmin 53->118; `second_apply_is_noop=True` |
| 4. RE-APPLY | `align_role_contract --apply` | a full NOOP: 0 additions, 0 removals, counts unchanged | exit 0; both roles `status=aligned`, 0/0, 135 and 118 unchanged; `reapply_digest_unchanged=True` |

The apply and the re-apply produced **identical** `after_permissions` for both roles.

## STOP conditions for the production run

Any one of these stops the cutover at this step:

* `unapproved_additions_total` is not 0, or `unapproved_removals_total` is not 0.
* `warnings` non-empty - including a deactivated or organisation-bound `orgadmin` /
  `projectadmin` document, or a refusal to change the role because holders could not be read
  or the authority change could not be announced (exit 2).
* `proposed_removals` for either role is anything other than exactly `["billing.plan.manage"]`.
* The Class A addition count differs from 82 / 66, or the resulting count differs from
  135 / 118, **without** an explained production data change since 2026-09-20.
* Any role outside `ALIGNED_ROLE_IDS` appears in the report as changed.
* `second_apply_is_noop` is not `true`.

## Ordering

Run it **after** the catalogued migrations and **while the application tier is still
stopped** (`PRODUCTION_CUTOVER_CHECKLIST.md` section 14). With nothing running there is
nothing cached to revoke, so the D4-B revocation path has no live race to lose.
