# Graph Letter ownership — migration & backfill plan (G32)

**Status:** PLAN ONLY. Not executed. Must not run against production without a
separate explicit task and its own approval.

## Why

`(:Letter {normCode})` is MERGEd on `normCode` alone, so it is **global** —
shared by every document, in every tenant, citing the same code. The writer
used to `ON MATCH SET` document/tenant/perspective properties onto it, so the
last document to sync overwrote everyone else's (reproduced cross-tenant
against a real FalkorDB: `organization_id` org-A → org-B).

The writer is fixed (it now writes only `normCode`/`createdAt`/`lastUpdated`),
but **removing the write does not clean anything** — it freezes whichever
tenant wrote last. An independent review measured the deployed graph:

| Property on shared node | Nodes carrying it (of 215) |
|---|---|
| `code` | 215 |
| `direction` | 215 |
| `subject` | 37 |
| `date` | 36 |
| `project` | 37 |
| `organization_id` | 10 |
| `project_id` | 10 |

Every one of those is owned by some document; on a shared node they are one
tenant's value served to all.

## Ownership model (ADR)

**Chosen: the shared node is identity + topology only.** Document/tenant data
stays canonical in Mongo and is resolved per read through the certified
authority foundation.

Rejected: a `(:LetterSupport {document_id, …})` node or a `SUPPORTS_LETTER`
edge carrying the owned properties. Both were considered and rejected because
they would (a) duplicate into FalkorDB data Mongo already owns, creating a
second source of truth that can drift and must itself be authority-gated;
(b) require migrating 215 nodes' worth of owned data *into* the graph before
readers could be corrected — strictly more risk than reading Mongo; and
(c) give the graph its own authority surface, which the standing rule forbids.
The graph answers "which codes relate to which" — never "what does this letter
say".

Edges keep an `owner_document_id` (added) because reconciliation must delete
only the writing document's own edges; that is provenance for a *write*
decision, not content.

## Migration — additive, reversible, in this order

**Stage 0 — snapshot.** `GRAPH.COPY contraclaim contraclaim_pre_g32` (or a
`redis-cli --rdb` dump). Record node/edge counts. This is the rollback.

**Stage 1 — readers first (already done in code).** Consumers resolve content
from Mongo and never read node properties. This makes the contaminated
properties *inert* before anything is modified. Deploy and validate here; a
rollback at this stage is a code revert only, with no data change.

**Stage 2 — validate inertness.** For a sample of the 37 nodes carrying
`subject`, confirm the value appears in no API response, drafting prompt,
report row or export. Automated form: the marker tests already added
(`test_graph_consumer_authority`, `test_graph_letter_ownership_falkor`).

**Stage 3 — backfill edge ownership.** Existing edges have no
`owner_document_id` (measured: 307 of 310 `CITES` edges in the live graph), so
scoped cleanup cannot match them and is a permanent no-op for the legacy corpus.

**Do NOT backfill to `''`.** An earlier draft of this plan proposed
`SET e.owner_document_id = coalesce(e.owner_document_id, '')` and claimed an
unattributed edge would never be auto-deleted. That is wrong: `''` is a
*concrete* value, and it is exactly what `upsert_letter_with_refs` synthesizes
when a caller omits ownership (`falkor_graph_service.py`, the
`str(owner_document_id or ... or "")` fallback). Backfilling to `''` would park
every legacy tenant's edges under one shared owner that a single ownerless
`cleanup=True` sync then wipes — re-arming the exact CRITICAL this work closed.
An independent review reproduced that: 3 legacy tenant edges → 0 after one call.

Two defences are already in the writer: the owner is part of the edge MERGE key
(so overlapping citations are separate assertions), and `cleanup=True` is
**skipped entirely** when the resolved owner is empty.

Backfill only where ownership is genuinely derivable from Mongo `references`:

    // per (document, normCode, target) triple derived from Mongo
    MATCH (s:Letter {normCode: $srcNorm})-[e:CITES]->(d:Letter {normCode: $dstNorm})
    WHERE e.owner_document_id IS NULL
    SET e.owner_document_id = $documentId

Leave the remainder **NULL**. A NULL-owned edge is unreconcilable but safe —
cleanup's `e.owner_document_id = $ownerDocumentId` never matches NULL — which is
the correct direction for data whose provenance is genuinely unknown.

**Stage 3 consequence — retraction is now ownership-only.** Cleanup previously
also required `e.source IS NULL OR e.source IN ['parser','manual']`. That
allow-list was removed: the writer emits an *open* set of source tags (callers
pass arbitrary `ref["source"]`, and `graph_ingestion_service` emits `'system'`
for `previous_letter_id`), so every unlisted tag produced an edge that was
written but could never be retracted — a permanent stale graph fact. Retraction
authority is ownership, which is in the edge MERGE key.

The consequence for this stage: backfilling `owner_document_id` onto an edge
makes it retractable by that owner's next sync **regardless of its source tag**.
That is intended — an owner's current reference set is authoritative — but it
means Stage 3 must only attribute edges to an owner whose Mongo `references`
genuinely derive them. Attributing a hand-curated edge to a document whose
parser will not re-derive it will delete that edge on the next sync. When in
doubt, leave the owner NULL: unreconcilable-but-inert beats wrongly-attributed.

**Stage 4 — optional property cleanup, last and separately.**

    MATCH (l:Letter)
    REMOVE l.subject, l.date, l.code, l.direction,
           l.organization_id, l.project_id, l.project

Only after Stage 2 proves nothing reads them. Idempotent (`REMOVE` of an absent
property is a no-op) and resumable — interrupt and re-run.

**Stage 5 — drop the stale indexes** on `Letter.date/direction/organization_id/
project_id`, and remove their creation from `ensure_schema`.

## Properties

- **Idempotent:** every stage is re-runnable; `MERGE`/`REMOVE`/`SET` converge.
- **Interruptible:** batch Stage 4 with `LIMIT` + repeat; partial completion is
  a valid intermediate state because readers already ignore the properties.
- **Rollback:** Stages 1–3 revert by code revert / property restore from the
  Stage 0 snapshot. Stage 4 is the only lossy step — it is deliberately last,
  optional, and gated on Stage 2 evidence.
- **Duplicate support:** two documents citing one code is the normal case and
  needs no dedup — identity is shared by design; support lives in Mongo.
- **Canonical preservation:** no stage deletes a `:Letter` node. A node with no
  live supporter is left in place; serving eligibility (not deletion) is the
  safety boundary.

## Validation

Post-migration: node count unchanged (215); zero nodes carrying any
non-allowlisted property; every edge has `owner_document_id`; the real-FalkorDB
suites (`ownership`, `writer_authority`, `stale_containment`, `invalidation`)
green against the migrated graph.

## Not covered here

`Clause` nodes carry `text_content` in the graph and are never deleted; a
hard-deleted contract's clause text remains resident. That is a separate,
tracked finding — it needs its own containment decision before any migration.
