# Key Date Baseline and Successive EOT Revision Workflow

## Current behaviour

- Key dates are stored as one mutable milestone document per project. The original date is retained, but ordinary milestone edit, delete, CSV import, and recalculation are not protected by a project-level frozen-baseline state.
- EOT applications are milestone-specific. Submission and client review share one lifecycle and there is no project/contract EOT revision entity spanning the affected milestones.
- The extension-history revision number is derived from each milestone's approved-revision count. Pending EOT submissions therefore have no stable EOT-N identity and cannot be represented consistently across a project.
- Approval immediately updates `current_approved_key_date`; there is no separate submission lock or determination freeze.
- Register exports show the current milestone projection and approved history only. They cannot reproduce a frozen baseline, a pending submission, a combined determination, or complete dynamic EOT lineage.

## Root limitations

1. A pending EOT is not a durable project-level revision and cannot safely coexist with later EOT revisions.
2. Contractor submission and client determination are collapsed into the same record and status.
3. There is no immutable snapshot of the contractual date applicable when each EOT item was submitted.
4. Baseline immutability is not enforced on every write path.
5. Revision-number allocation has no database uniqueness or atomic project/contract counter.
6. CSV and export workflows are tied to the current milestone projection rather than revision-owned fields.

## Revised lifecycle

```text
Draft Original Baseline
  -> Frozen Original Baseline (immutable snapshot, revision 0)
  -> EOT-1 draft/submitted/locked
  -> EOT-2 draft/submitted/locked even while EOT-1 determination is pending
  -> EOT-N without a hard-coded limit

Each submission may be linked to an independent or combined determination.
A determination changes a milestone's current contractual date only when it is
frozen and that milestone item is granted/partially granted with a grant date.
```

## Data model and migration

- `key_date_baselines`: one organization/project/contract baseline, immutable frozen milestone snapshot, atomic `next_revision_number` counter.
- `key_date_eot_submissions`: project/contract revision metadata and independent submission status.
- `key_date_eot_submission_items`: affected milestones, contractual date at submission, submitted date, claimed days, and remarks.
- `key_date_eot_determinations`: determination metadata, links to one or more EOT submissions, result status, freeze metadata, and explicit supersession links.
- `key_date_eot_determination_items`: milestone-level grant date, granted days, result, remarks, and the contractual date before determination.
- A forward-only migration creates scope, unique-revision, item, and determination indexes. Existing legacy milestone/EOT records are preserved; no ambiguous historical record is rewritten automatically.

## API and UI changes

- Add workflow summary/revision-history endpoints, baseline freeze, EOT create/update/lock, determination create/update/freeze, revision CSV preview/import, and baseline/submission/determination/history export operations.
- Keep legacy milestone endpoints for compatibility, but block baseline-changing writes after freeze and direct frozen projects to the revision workflow.
- Add a project-level workflow panel on `/key-dates` showing current contractual baseline, latest submission, pending determinations, baseline controls, EOT creation, independent locks, determinations, and downloads.
- Keep the main grid compact. Detailed lineage and revision-owned actions remain in the workflow/history view.

## CSV and export controls

- Original CSV continues to use selected organization/project scope and is rejected once the baseline is frozen.
- EOT submission CSV accepts only `milestone_ref,eot_submitted_date,claimed_extension_days,remarks`.
- Determination CSV accepts only `milestone_ref,eot_granted_date,granted_extension_days,determination_result,remarks`.
- Historical/context columns in templates are regenerated from the database and ignored/rejected as editable input.
- Complete-history exports generate EOT-N columns dynamically from stored revisions.

## Security, integrity, and audit

- Backend permissions separately gate baseline freeze, EOT submission lock, determination entry, determination freeze, and export.
- Every resource is authorized against the canonical organization/project scope stored on the resource.
- Atomic baseline counters plus a unique organization/project/contract/revision index prevent duplicate EOT numbering.
- Lock/freeze operations are idempotent and filter on eligible states. Locked/frozen records have no ordinary edit path.
- Audit events record actor, organization, project, contract, revision, action, and source.

## Compatibility and risks

- Existing approved legacy EOT dates remain effective and visible. They are not silently converted into project-level EOT submissions because their original cross-milestone grouping cannot be inferred safely.
- Mongo multi-document creation is guarded by validation, unique indexes, and cleanup on partial failure. Production runs on the existing replica set; the migration provides the hard integrity constraints.
- Browser acceptance requires an authenticated organization/project with permission to freeze and determine EOTs. Unit/integration tests will prove the contractual rules independently of that environment.

## Verification gates

- Backend tests for frozen baseline immutability, EOT-2/EOT-3 while prior submissions are pending, independent snapshots, later EOT-1 partial grant, rejection/no-change carry-forward, combined determination, CSV protection, RBAC, and duplicate revision protection.
- Frontend tests for baseline labels, pending-count semantics, current contractual dates, and revision-history presentation.
- Build/typecheck plus browser validation of the required Original -> EOT-1 pending -> EOT-2 locked -> later EOT-1 partial grant scenario when a safe local authenticated environment is available.
