# Correspondence Linking CL-1 — Canonical Relationship Hardening

**Date:** 21 September 2026
**Base:** `release/contraclaim-rc1` @ `99ab522`
**Scope:** `entity_document_links`, `DocumentRelationshipService`, `EntityAdapterRegistry`,
`routers/document_relationships.py`, the `/document-search` link selector. No new register UI.

CL-1 stabilises the canonical correspondence ↔ register relationship framework before it is
extended to Variation, Hindrance or other registers (CL-2).

## 1. Defects fixed

### 1.1 ObjectId-keyed Documents answered 500 after committing (P0-1 / P0-2)

`DocumentRelationshipView.document` embedded the raw Mongo Document row. Production Documents are
ObjectId-keyed (`document_service` pops any supplied `_id`), and a `Dict[str, Any]` field validates
an `ObjectId` happily and then fails at FastAPI response serialization — *after* the handler
returned and the transaction committed. Reproduced against a disposable replica set with real RBAC:

| Operation | Old response | Row / audit state |
|---|---|---|
| `POST …/document-links:batch` | 500 | link committed, `linked` audit committed |
| `GET …/document-links` | 500 | — |
| `GET /documents/{id}/entity-links` | 500 | — |
| `GET /document-links/{id}/history` | 500 | — |
| `POST /document-links/{id}:remove` | 500 | removal committed, `unlinked` audit committed |

A string-keyed Document failed the same way whenever any nested field held an `ObjectId`.

**Fix.** One presentation boundary: a `model_validator(mode="before")` on
`DocumentRelationshipView` passes its whole input through `utils/bson_presentation.present_bson`,
which is total by construction (ObjectId/UUID → str, Decimal128/Decimal → exact string, binary →
`null`, datetimes kept, unknown types → `str`) and rebuilds containers rather than editing them, so
the canonical row a caller holds is never mutated. No router converts ids.

`link_batch` and `freeze` now return raw rows from the transaction callback and present them only
after the commit, so building a response can neither abort a write nor be mistaken for its failure.
No committed relationship is ever rolled back for a presentation reason.

### 1.2 A link whose Document row was hard-deleted could not be removed (P0-3)

`remove()` resolved the Document fail-closed and answered 404, leaving an un-removable orphan.

**Fix.** When — and only when — the Document row is absent, the Document-side decision is still
taken: `DOCUMENT_VIEW` is authorized against the link's stored organisation/project. Before that,
`_require_link_scope_matches_target` requires the stored scope to equal the target record's scope
(which the actor was separately authorized to *manage*), so a link row claiming another project is
refused with 409 rather than trusted. The deleted Document is never recreated. When the Document
row exists, the unlink path is unchanged from before CL-1 (the Document's own scope must equal the
target's, then `DOCUMENT_VIEW`). Authorization is
therefore no weaker than for a present Document: target `manage` + `DOCUMENT_VIEW` in the same scope.

### 1.3 Contract Document target returned 500 on every write (P0-4)

`ContractDocumentEntityAdapter` was registered (Contract Master ticket 06 deliberately made
project-scoped instruments relationship targets) but inherited the abstract
`guard_relationship_write` / `delete`, so every link raised `NotImplementedError`.

**Disposition: implemented (option A).**

* `guard_relationship_write` bumps only `document_relationship_revision`, fenced on the same
  structural anchor `load` read (`organization_id`, `scope_level: project`, `scope_project_id`), so an
  instrument re-scoped between load and commit refuses the write.
* `delete` always refuses: deleting an instrument is a Contract Master lifecycle act.
* `freeze` refuses explicitly (`supports_freeze` was already false).
* Deep link: the old `/contract-documents/{id}` route never existed and the Contract Master workspace
  does not read an instrument id. The target now links to `/contracts/viewer/{document_id}` — the live
  contract viewer, keyed by the instrument's canonical Document (`ContractDocument.document_id`).
* Permissions: `dms.contract.master.view` / `.manage`, project-scoped only. Organisation-scoped
  instruments are still refused by the generic project-less guard (409).
* The fence counter is declared on `ContractDocumentRecord` (`document_relationship_revision`,
  OPERATIONAL) so rows keep validating against that `extra="forbid"` model.

### 1.3a Target gate decided without the target's project (found by the CL-1 RBAC matrix)

`_target` authorized on the raw register row. A Contract Document row keeps its project in
`scope_project_id`, so `PolicyService` saw no project and decided at organisation level: a Project
Admin of **another** project got 200 on the instrument's link list (the Document check still
filtered every row, so no link leaked, but the gate itself was organisation-wide). The target gate —
and the delete and batch-view gates — now authorize `_authorization_subject(context)`: the row with
the adapter's canonical organisation/project. For every other adapter the values are identical.

The class of defect is pinned too: `test_every_registered_adapter_implements_its_write_seams` fails if
any registered adapter inherits an abstract seam.

### 1.4 Correspondence roles accepted any Document (P1)

A contract-type Document could be linked as `correspondence`.

**Definition.** Correspondence is the `uploadType` taxonomy's `incoming | outgoing` (case-insensitive,
as the Document model accepts; legacy `upload_type` read only when `uploadType` is absent). A missing
or unrecognised value is **not** correspondence (fail closed).

**Enforcement.** For the roles that assert the Document *is* correspondence — `correspondence` and
`payment_correspondence` (`CORRESPONDENCE_ROLES`) — `link_batch` refuses any other Document with 422,
checked on the preflight read and again on the in-transaction read. Every other role
(`supporting_document`, `notice`, `determination`, …) is unrestricted. Every target that offers a
correspondence role also offers `supporting_document`, which the 422 message points to.

This applies to every `link_batch` caller, including the legacy backfill: a legacy letter id that
resolves to a non-correspondence Document is refused instead of being mislabelled. The letter
backfill inventory (dry run) now classifies such a candidate `non_correspondence` instead of `valid`,
so the dry run and the apply agree. How many production legacy letters lack `uploadType` has not
been measured (read-only inventory is a CL-2 pre-step).

## 2. Link selector (`/document-search`)

Verified over real HTTP with real seeds:

* organisation scope — a foreign-organisation filter answers 403; no foreign Document is returned;
* project scope — a project-tier user never receives another project's Documents;
* linkability — `linkable_only` constraints (project-bound, not duplicate/deleted/under review);
* new: `uploadType=correspondence` matches `incoming | outgoing` (case-insensitive). The typed client
  accepts the value; the `EntityDocumentLinks` component does not send it yet (CL-2). Server-side
  enforcement (§1.4) does not depend on the filter.

## 3. RBAC matrix (real seeds, real `PolicyService`)

Claim target in the actor's project; Document ObjectId-keyed. "Reverse" is status / links visible.

| Persona (role reference) | View links | Create link | Remove link | Reverse lookup |
|---|---|---|---|---|
| Project User (`projectuser`) | 403 | 403 | 403 | 200 / 0 |
| Project Admin (`projectadmin`) | 200 | 201 | 200 | 200 / 1 |
| Organisation User (`orguser`) | 403 | 403 | 403 | 200 / 0 |
| Organisation Admin (`orgadmin`) | 200 | 201 | 200 | 200 / 1 |
| System User (no such role; `account_type=system_service`, no role) | 403 | 403 | 403 | 403 |
| Super User (`superuser`, dormant, no role document) | 403 | 403 | 403 | 403 |
| Super Admin (`superadmin`) | 200 | 201 | 200 | 200 / 1 |

Project User and Organisation User hold `dms.document.view` but no `dms.claim.*` permission in
`DEFAULT_ROLES`, so they cannot open a Claim's links and the Claim is filtered from their reverse
lookup. That is the current seed, measured — not a CL-1 decision.

Contract Document target (instrument in the actor's organisation, project A1):

| Persona | View links | Create link |
|---|---|---|
| Project User / Organisation User | 200 | 403 |
| Project Admin (A1) / Organisation Admin / Super Admin | 200 | 201 |
| System User / Super User | 403 | 403 |
| Foreign-organisation Admin / Project Admin of A2 | 403 | 403 |

View is `dms.contract.master.view`, which the permission alias contract makes equivalent to the
legacy `projects:read` that Project/Organisation User hold — measured, not a CL-1 decision.

## 4. History endpoint

`GET /document-links/{id}/history` has only ever returned the link's **current row** (including a
removal tombstone): links are soft-removed in place and no revisions are stored. It was misnamed.

* New truthful route: `GET /document-links/{id}` → the current state of one link.
* `/history` is kept for compatibility, marked `deprecated` in OpenAPI, and answers with an
  RFC 9745 `Deprecation: @1789948800` (2026-09-21) and a `Link: …; rel="successor-version"` header.
* The durable change record is the audit trail: `document_relationship.linked` / `.unlinked`, each
  now carrying `metadata = {target_type, target_id, document_id, relationship_role}` alongside actor,
  organisation, project, link id, timestamp and `before` / `after`. One operation emits one event; an
  idempotent re-link emits none.

## 5. Follow-up debt recorded, not changed

* `backend/rbac_backend/documents.py` — a 1,863-line orphaned document pipeline with a raw
  `delete_one`. It is not mounted and is **unimportable** (its module-level relative import reaches
  beyond the top-level package); `test_canonical_document_lookup.py` and
  `test_upload_limit_enforcement.py` pin that. Left in place for CL-1; removal is a separate change.
* `EntityDocumentLinks` should send `uploadType=correspondence` when a correspondence role is
  selected, so the selector stops offering Documents the server will refuse.
* `GET /document-links/{id}` for a link whose Document row is gone still answers 404 (only removal
  was made robust).
* The embedded `document` is still the full Document row (values made JSON-safe, fields not
  projected). The viewer holds `DOCUMENT_VIEW`, so this is not an authorization leak, but an explicit
  presentation field list would be tighter.
* The selector's `uploadType=correspondence` filter matches `uploadType` only; the server also
  accepts the legacy `upload_type` when `uploadType` is absent. The server is the authority; the
  selector may under-offer such legacy rows.
* `delete_target` for a Contract Document answers the generic 409 "Relationship target changed…"
  (it is not reachable over HTTP today; no register route deletes instruments through it).

## 5a. CI coverage of the real-Mongo suite

The real-Mongo / real-RBAC suite is opt-in, like every other `*_TRACER_MONGODB_URI` suite: CI's
Mongo service is a standalone `mongod`, and relationship writes need transactions (a replica set).
In default CI it **skips**. The always-on `test_document_relationship_cl1.py` pins the same defects
against the shared fake store, but it cannot prove the RBAC matrix, isolation or search leakage —
those were proven locally against a disposable replica set. Running this suite in CI needs a
replica-set Mongo service in the workflow (CL-2 / CI hardening item).

## 6. Verification

* `tests/integration/test_document_relationships_cl1_mongo.py` — opt-in
  (`RELATIONSHIP_CL1_MONGODB_URI`, disposable replica set): real JWT → `get_current_user`, real
  `PolicyService`, real role/permission seeds, no dependency overrides. 33 tests; the original 24
  failed 20 against the old implementation, and the Contract Document matrix caught the target-gate
  scope gap (§1.3a) before its fix.
* `tests/test_document_relationship_cl1.py` — always-on fake-store guards for the same defects.
