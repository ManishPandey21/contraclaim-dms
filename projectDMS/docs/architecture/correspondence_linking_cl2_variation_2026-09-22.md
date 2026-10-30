# Correspondence Linking CL-2 — Variation, Link Selector, Deep Links

**Date:** 22 September 2026
**Base:** `feat/cl1-relationship-hardening` @ `19775a2` (CL-1, draft PR contraclaim-dms#23)
**Scope:** Variation on the canonical `entity_document_links` framework; correspondence-aware link
selector; "Link to Record" from the Document Viewer; reverse-lookup deep links. Hindrance, Programme
and Chronology are out of scope.

## 1. Variation adapter

`VariationEntityAdapter` (`services/entity_adapter_registry.py`), target type **`variation`** (the
router's existing `resource_type`), collection `variations`.

| Concern | Decision |
|---|---|
| Load | `variations._id` via `document_id_candidates` (string or ObjectId) |
| Scope | `organization_id` / `project_id` on the row; a row missing either is refused by the generic 409 |
| Permissions | view `dms.variation.view`, manage `dms.variation.edit`, delete `dms.variation.delete` |
| Write guard | `$inc document_relationship_revision`, fenced on `_id` + org + project read by `load` |
| Delete | `delete_one` fenced on the same scope, called only from `delete_target` |
| Freeze | not supported (the Variation lifecycle has no evidence-freeze step) |
| Deep link | `/variations?variation_id={id}` — consumed by `VariationRegisterPage` |
| Label | `variation_number`, else the id |
| Legacy read-through role | `manual_review` |

**Roles** (derived from the model and status workflow `draft → submitted → under_review →
recommended → approved/rejected`): `variation_submission` (backs `submitted_amount`),
`variation_approval` (backs `approved_amount` / `approval_date`), `correspondence`
(`letter_reference`), `supporting_document`. The model has no instruction or quotation concept, so
neither role was added.

## 2. Raw `linked_document_ids` writes refused (RED-first)

Reproduced before the fix: `PUT /variations/{id}` with a foreign-organisation Document id answered
**200 and persisted the forged id**.

Now:

* `POST /variations` with a non-empty `linked_document_ids` → **409**, nothing created. An empty array
  is a no-op and is not stored.
* `PUT /variations/{id}` with `linked_document_ids` → **409** unless the value is set-equal to the
  stored legacy array (a client echoing the whole record back). Adding, forging, clearing or
  replacing is a relationship write and goes through `/entities/variation/{id}/document-links`.
  Nothing is persisted on refusal — not even the other fields in the same request.
* `VariationService.create/update` refuse the field too (`AmbiguousLegacyVariationRelationshipError`),
  so a direct service caller cannot bypass the router.
* Reads present `linked_document_ids` as the viewer's *authorized* ids (canonical links plus legacy
  ids whose Document is in the Variation's own scope and viewable). Forged/foreign legacy ids stay in
  the row but never surface. A scope-less legacy row stays readable with `[]`.

## 3. Legacy backfill (`module: "variation"`)

`services/variation_document_link_migration.py`, registered as a `WRITE_BACKFILL` module on the
existing operator (`/legacy-relationship-backfill/inventory|apply`: DMS admin + step-up, scoped,
dry-run by default, ≤ 50 selections, lease-claimed, idempotent, audited). Nothing in either legacy
field is rewritten or removed — canonical links are added beside them.

| Source | Classification | Role |
|---|---|---|
| array id → in-scope incoming/outgoing Document | `valid_correspondence` (`ambiguous_role`) | operator chooses |
| array id → in-scope other Document | `valid_supporting_document` | `supporting_document` (Claim precedent) |
| array id → other org / other project | `cross_organisation` / `cross_project` | refused |
| array id → no row / deleted / blocked | `missing_document` / `deleted_document` / `blocked_document` | refused |
| repeated / malformed id; row without project | `duplicate` / `invalid_id` / `project_null` | refused |
| `letter_reference` → exactly one in-scope correspondence Document | `valid_correspondence` | `correspondence` (Claim letter precedent) |
| `letter_reference` → several / none / non-correspondence / already in array | `ambiguous` / `missing_document` / `non_correspondence` / `duplicate` | refused |

`letter_reference` is matched on `letterNoNormalized` (the Falkor `normalize_letter_code` form) or the
exact `letterNo`, **only inside the Variation's own organisation/project** — letter numbers are not
unique across tenants. The correspondence-role 422 still applies on apply.

## 4. Production census (read-only, not run)

`python -m rbac_backend.scripts.variation_legacy_link_census [--organization-id X] [--project-id Y]`
(inside the backend container). Counts only: Variation rows, rows with `linked_document_ids`, total
linked ids, valid correspondence / supporting, missing, deleted, blocked, foreign-org, foreign-project,
duplicate, invalid ids, the resolved Documents' `uploadType` (incoming / outgoing / contract / other /
missing / unresolved), `letter_reference` resolution, and existing canonical Variation links. No
identifier, letter number, subject or name is printed. The database handle is wrapped so every write
method (and `aggregate`, `command`) raises before reaching the driver. **CL-2 did not query production.**

## 5. Frontend

* **Variation Register** — a "Correspondence" action per row opens the shared `EntityDocumentLinks`
  (no second linking UI), permission-aware on `dms.variation.edit`; `?variation_id=` opens it directly.
* **Selector** (`EntityDocumentLinks`) — for `correspondence` / `payment_correspondence` it sends
  `uploadType=correspondence` and offers no Contract direction; every other role sends no type filter
  (contract/supporting files stay selectable). Filters: free text (letter no / subject / party via
  `q`), direction, exact letter number, date from/to. Party has no backend filter; it is searched via
  `q` and shown. Results and linked rows show letter number, subject, direction, party and date.
* **Link to Record** (`LinkToRecordDialog`, from the Document Viewer's Records tab) — register type →
  record → role. Backed by the new `GET /documents/{id}/link-targets?target_type=`, which offers only
  the eight verified types (claim, ipc_bill, insurance, bank_guarantee_event, key_date_achievement,
  eot_submission, eot_determination, variation), only records in the Document's own org/project, only
  where the caller holds the target's manage permission. The write still goes through
  `document-links:batch`. The dialog stays open so one letter can be linked to several records.
* **Deep links now consumed:** Insurance `?insurance_id=` (opens the policy, fetching it if filtered
  out), Key Date `/key-dates/{id}?achievement_id=` (focuses the achievement evidence),
  `/key-dates?project_id=&submission_id=|determination_id=` (selects the project and focuses that
  event's evidence), Contract Document `/contracts/viewer/{document_id}` (the viewer loads that
  Document — pinned by a test; no `/contract-documents/{id}` route was created).

## 6. Verification evidence

* `tests/test_variation_document_relationships.py`, `tests/test_variation_legacy_relationship_backfill.py` — always-on.
* `tests/integration/test_variation_relationships_cl2_mongo.py` — real JWT, real `PolicyService`, real
  seeds, ObjectId Documents, disposable replica set (`RELATIONSHIP_CL2_MONGODB_URI`): the 10-step
  workflow for incoming and outgoing, 422 / foreign refusals, many-to-many, delete safety, the
  seven-persona RBAC matrix, isolation (foreign org/project Variation, foreign correspondence, foreign
  link id, cross-project unlink, reverse, Link-to-Record and selector leakage).
* CI: a disposable single-node replica set now runs the CL-1 and CL-2 real-Mongo suites in a step that
  **fails if they skip**.
* Vitest: selector, Variation page, Link to Record, deep links, contract viewer; Playwright
  `e2e/correspondence-linking.spec.ts` (stateful mock, listed in `MOCKED_SUITES`, so it is excluded
  against staging and is not deployment evidence).

RBAC matrix (Variation target, real seeds):

| Persona | View links | Create | Remove | Reverse |
|---|---|---|---|---|
| Project User / Organisation User | 403 | 403 | 403 | 200, Variation hidden |
| Project Admin / Organisation Admin / Super Admin | 200 | 201 | 200 | 200 |
| System User / Super User (dormant) | 403 | 403 | 403 | 403 |

Project/Organisation User hold `dms.document.view` but no `dms.variation.*` in `DEFAULT_ROLES` —
measured, not a CL-2 decision.

## 7. Debt recorded, not changed

* **CROSS-MODULE ACTIVE-SCOPE CONSISTENCY DEBT.** The selected-project tenant-context mechanism is being
  built for Hindrance in PR #22 and is not in this base. Variation keeps its existing semantics
  (`build_scope_query` + explicit `project_id` filter); it must consume the shared mechanism once that
  lands.
* Link-to-Record scans at most 200 rows per register per query (a DB-side label prefilter narrows first
  where the label is a row field). Bank Guarantee event and Key Date achievement labels come from their
  parent rows, so search on those types is limited to the first 200 in-scope rows.
* The Document Viewer normalises `uploadType` to incoming/outgoing for display, so Link to Record does
  not pre-filter correspondence roles by the Document's type; the server's 422 is shown instead.
* `document-search`'s `subject` filter is a raw regex (pre-existing, `authorization_service`).
* The Playwright spec is mock-backed; the final staging run of the same workflow is still owed.
* `GET /document-links/{id}` for a link whose Document row is gone still answers 404 (CL-1 debt).
* The "Link to Record" button is shown to every Document viewer; the server lists only records the
  caller can manage, so an unprivileged viewer sees an empty list rather than no button.
* `VariationEntityAdapter.legacy_targets_for_document` (reverse read-through) filters by org and
  project, so a scope-less legacy Variation row never appears in a Document's Linked Records.
* A role holding `dms.variation.edit`/`delete` without `dms.variation.view` would get 403 on the
  post-write presentation. No seeded role is shaped like that.
* The present / refuse / echo pattern for legacy arrays is now repeated in the IPC, Bank Guarantee,
  Key Date and Variation routers; a shared helper belongs in CL-3 before more registers copy it.
* `LINK_TO_RECORD_TARGET_TYPES` is a second list beside the registry; an adapter flag would remove it.

## 8. Independent reviews and dispositions

Two independent reviewers (security/RBAC + standards; spec fidelity) reported **0 BLOCKER / 0 HIGH**.

| Finding (severity) | Disposition |
|---|---|
| Scope-less legacy Variation became undeletable: `delete_target` answers 409 for targets without scope (MEDIUM) | **Fixed** red→green: such a row can hold no canonical link, so it is deleted directly and audited `variation.deleted` |
| Link-to-Record listing is unbounded work per keystroke (MEDIUM) | **Fixed**: scoped `RateLimiter(scope="document_link_targets")`, 120/min per user; DB-side label prefilter; scan cap documented |
| PUT echo compared with the raw stored array: a client echoing what it was shown got 409, and set equality was an oracle for hidden legacy ids (LOW-MEDIUM) | **Fixed**: the echo is compared with the *presented* ids only |
| Playwright retries not pinned outside staging mode (MEDIUM) | **Fixed**: the spec pins `retries: 0`, serial; evidence run used `--workers=1 --retries=0` |
| Playwright is mock-backed (MEDIUM) | Accepted and stated: listed in `MOCKED_SUITES`; staging run still owed |
| Census typed duplicate/invalid entries as `unresolved` (LOW) | **Fixed**: only resolved first occurrences are typed |
| Census read-only wrapper guards accidents, not misuse (LOW) | Docstring says so; recommends a read-only Mongo user |
| Subject filter not exposed (LOW) | **Fixed**: Subject field added |
| Failed-link rollback untested (LOW) | **Fixed**: test added |
| Remaining LOWs | Recorded in §7 |
