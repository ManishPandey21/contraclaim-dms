# Contract Master v1 — upload and migration

Current state. See [00](contract_master_v1_00_overview.md) for the governing rules.

Both paths converge on **one control plane**: a reconciliation candidate that an
operator promotes. New uploads simply arrive with their scope already resolved
instead of ambiguous.

---

# Part A — upload

## Explicit scope capture

A contract upload carries a **required discriminator**:

```
scope_level : "organization" | "project"      required, no default
project_id  : required iff scope_level == "project"
              rejected (422) iff scope_level == "organization"
```

Permanently rejected as scope signals: an omitted `project_id`, an empty
`project_id`, an active navbar selection, a cleared navbar selection, a role
tier, and any client-persisted selection.

The discriminator is **sent affirmatively** as a non-empty value. It cannot be a
nullability: a falsy value is omitted by ordinary client serialisation, so
"empty" and "absent" are indistinguishable on the wire before any server code
runs.

The request should be a **discriminated union**, making the invalid states
unrepresentable in the client's own type system — there is then no way to express
"project scope with no project", and no empty string or undefined to mis-handle.

## Unknown scope is not an upload option

`UNKNOWN` belongs to migration and reconciliation only.

At upload a human is present, and that human is the cheapest adjudicator the
system will ever have. Deferring converts a two-second choice into a review-queue
item, and the review queue is the migration bottleneck.

If scope genuinely cannot be determined, the correct action is to upload a
**generic Document, not a contract instrument**. It can become a
ContractDocument later through promotion. This keeps "I don't know" out of the
authoritative path entirely.

## What an upload produces

```
canonical Document
+ contract_document_reconciliation candidate
     scope_classification = PROJECT_SCOPE_RESOLVED | ORG_SCOPE_CONFIRMED
     scope_evidence       = [explicit_upload_intent]
```

**An upload does not create an authoritative ContractDocument, and does not
create applicability.** It creates a deterministic promotion candidate — one that
needs no archaeology, which is the entire point.

A selected contract in an upload form is **operational context, never legal
applicability**. Project selection is a structural scope anchor, not `APPLIED`.

## Durability

Scope intent is copied into the durable reconciliation row **at document
creation**. The upload session carries a TTL and is **never re-read for scope
afterwards** — evidence is recorded when observed, not re-derived later from a
store that deletes itself.

The audit event records `scope_level`, the anchor, actor, organisation, session
id and timestamp. **Audit is evidence and history, never the canonical ownership
field.**

## Retry and correction

A retry compares against the original session's `scope_level` and **rejects a
conflict** rather than creating a second record. One upload is one scope
decision; a different scope is a correction needing its own action.

- **Before promotion** — scope correction is a reconciliation **adjudication**,
  recorded and audited. Not a metadata edit.
- **After promotion** — `scope_level` and the project anchor are **IMMUTABLE**.
  Changing an instrument's reach would retroactively alter what evidence it could
  have supported. Correction is controlled replacement or supersession; that
  workflow is not designed in v1.

## Type at upload

A type may be captured. It becomes `TYPE_RESOLVED` only if the actor holds
`CONTRACT_MASTER_MANAGE`; otherwise `TYPE_SUGGESTED`. **Scope certainty does not
imply type certainty.**

## Batches

A multi-file batch shares **one scope decision and one project anchor**. **Type
is per file** — a batch routinely contains a GCC and an SCC, and forcing one type
across a batch would manufacture exactly the misclassification reprojection has
to repair.

The multipart route is the only bulk path, and it carries the same required
discriminator as a single upload.

## Compatibility

`scope_level` is required. An omitted discriminator is **rejected**, never
reinterpreted. Silent reinterpretation would fabricate the very evidence the
legacy corpus provably lacks.

---

# Part B — migration

## Control plane: borrow the pattern, not the module

Contract migration **reuses the accepted legacy-backfill pattern and its generic
primitives, but does not register as a module in it.**

The backfill's central safety property is *server-derived target scope* — it
loads the canonical target and never trusts the legacy row for scope. **Contract
migration has no target to derive scope from: deriving the scope is the
deliverable**, and the thing being created is the ContractDocument itself.
Registering would require faking that invariant, would need a target id the
candidate does not have, and would need a multi-document transaction the module
contract has no seam for.

**Reused as common primitives:** the candidate claim and lease (owner token,
confirm/release, reclaim on expiry) · the inventory-only capability concept,
refused before selection, claim or audit · inventory/apply separation with
explicit selection, batch cap and run id · dry-run inertness *before* claiming ·
decision-free candidate identity · the run-stamp check so a loser never reports a
write it did not make · no-resurrection of deliberately removed records · legacy
fields untouched.

**Not reused:** server-derived target scope · the adapter-registry dispatch · the
post-commit audit debt (see *Promotion*, below).

## Evidence classes in the legacy corpus

**No legacy source is authoritative.** Nothing in the corpus states a
`ContractDocumentType` or a legal applicability.

| Class | Sources |
|---|---|
| **STRONG** | the canonical document row (identity, organisation) · a contract-master row's `(project_id, contract_id)` · the creation audit row |
| **SUPPORTING** | clause org/project/contract provenance · contract categories |
| **HEURISTIC** | filename · section-type detection · appraisal inference · clause `document_type` |
| **UNUSABLE** | vector payload · graph `section_type`/`priority` · `Document.project_id == ""` |
| **EPHEMERAL** | upload sessions (TTL) |

Ephemeral evidence is **captured at inventory time** into the reconciliation row
and never re-read at promotion — otherwise a candidate that was well-evidenced
when inventoried is silently downgraded once its session expires.

## Scope classification

States: `PROJECT_SCOPE_RESOLVED` · `ORG_SCOPE_CONFIRMED` · `AMBIGUOUS` ·
`INVALID`.

**`ORG_SCOPE_CONFIRMED` has no automatic path. It requires operator
adjudication.**

The upload path collapses a null and an empty project into the same value before
any durable record is written, so the surviving audit null records only *"no
project was in the upload session"* — an absence one level removed, produced by a
coercion that had already merged two different absences. It carries no statement
of organisational intent, because no intent marker exists anywhere in the corpus.

The audit null therefore lifts a candidate from *unknowable* to **`AMBIGUOUS`
with a recorded strong hint** that shortens operator review. It never promotes on
its own. Additionally, the audit write is best-effort, so an **absent** audit row
carries no information in either direction and must never be read as "therefore
project-scoped".

`project_id == ""` alone establishes nothing. Neither does a role, nor a
filename. Conflicting strong signals → `AMBIGUOUS`, and **zero authoritative
writes**.

## Type classification

States: `TYPE_RESOLVED` · `TYPE_SUGGESTED` · `TYPE_AMBIGUOUS` · `TYPE_UNKNOWN`.

`TYPE_RESOLVED` requires operator confirmation. **Nothing legacy reaches it
automatically.** Heuristic agreement is not resolution — the filename detector,
the appraisal table and the clause branch all read the same substring, so three
agreeing heuristics are one heuristic counted three times.

`TYPE_UNKNOWN` never maps to `other_contractual_document`.

## Independent axes

Scope and type classify independently; both are retained as they resolve.
**`AUTHORITATIVE` requires both**, plus a resolvable canonical Document and
ownership consistency. **No partial promotion.**

## Applicability is never created automatically

The corpus contains **no legal applicability evidence at all**. Clause provenance
proves where a clause was ingested; a contract-master row proves a contract
exists. Auto-creating `APPLIED` would manufacture a legal fact from an
operational one.

Controlled auto-promotion from "proven sources" was considered and rejected: no
source qualifies, so the mechanism would exist with an empty set of inputs,
waiting to be pointed at a weaker one.

## Promotion

**Organisation scope** — ContractDocument + classification fact + canonical
document association + promotion receipt. May complete with **zero
applicability**, landing at *catalogued / unassigned*. That is a valid final
state; no dummy applicability, no sentinel project or contract.

**Project scope** — the above **plus** the applicability aggregate and its
`APPLIED` event, written atomically in one operator action that supplies the
applicability explicitly. Otherwise the candidate stays reconciliation-only.
**There is no half-promoted project-scoped instrument.**

All-or-nothing. If any required authoritative write fails, no part survives.

**The promotion receipt is written inside the promotion transaction** — candidate
id → contract document id, scope evidence, type evidence, operator, timestamp,
source identities, run id, source fingerprint. The backfill could not do this
(its single write went through another service, outside the audit's transaction)
and deliberately accepted a durable write with no audit row. Promotion is already
transactional, so the receipt joins at no cost and the debt is not repeated.

Operator *adjudications* that move no authoritative state remain best-effort
audit.

## Candidate identity and concurrency

```
contract-master-migration:{source_module}:{canonical_document_id}
```

Decision-free by construction: it excludes the scope decision, the type decision,
the review outcome, the lease owner, the filename (mutable) and the upload id
(ephemeral). Two operators discovering the same source converge on one candidate.

The claim serialises concurrent promotion. The loser receives an in-progress
result, then on retry sees the promotion and is told what was recorded — never a
second ContractDocument, never a second `APPLIED`. Two operators with *different*
classifications resolve the same way: first confirmed claim wins, and changing it
afterwards is an explicit classification correction, not a second promotion.
**Never last-write-wins.** A unique constraint on the promoted identity and the
run-stamp check are backstops, not the primary control.

**A lease is operational state and can never become a legal fact.** Expiry,
reclaim and release change no applicability, no lifecycle event and no
classification.

## Revalidation

The reconciliation row stores a **source fingerprint** — canonical document id,
its processing/lifecycle/duplicate state, content hash, and a hash of the
captured evidence. Promotion **re-resolves and compares**; a mismatch returns
"revalidation required" and writes nothing. **The inventory snapshot is never an
authority token.**

## Blocked documents

Split on the two axes the publication policy already distinguishes:

- **quarantine** (confirmed duplicate, deleted) → **INVALID, no promotion.** The
  system has decided this is not a real distinct thing.
- **quality** (adverse processing state, review required) → **promotion
  proceeds; evidence stays denied.** Legal existence and current content
  usability are different facts, and a transient content judgement must not
  destroy a legal record.

## Invalid and terminal states

`INVALID` when the canonical document cannot be resolved, the document is
quarantined, sources contradict on organisation, multiple incompatible canonical
identities exist, or the source identity is corrupt.

**`INVALID` is terminal and never silently downgraded to `AMBIGUOUS`.** Leaving
it requires an explicit operator action with a recorded reason.

## Dry run, bounds and failure

**Dry run persists nothing at all** — not even a reconciliation row. Materialising
the inventory is a separate, explicitly named action, because a preview must
never seed durable state that carries operator adjudications.

No "migrate all" endpoint. Inventory → review → explicit selection → promote,
with a batch cap, operator scope and a run id.

| Failure | Result |
|---|---|
| type unresolved / classification service failure | no promotion |
| promotion transaction failure | nothing survives; claim released; retry converges |
| receipt write failure | inside the transaction — fails with it |
| adjudication audit failure | best-effort; no authoritative state moved |
| reprojection queue failure | **promotion commits**; projection `FAILED`; evidence disabled |
| vector or graph unavailable | **promotion commits**; evidence disabled |

**A derived-store outage never vetoes valid authoritative promotion, and never
creates evidence authority.** Both directions matter.

## Post-promotion sequencing

Promotion sets `classification_revision = 1` and `projection_status = PENDING`.
Reprojection follows; evidence capability follows that.

The one-time re-embed required by removing classification from the embedding
input is **part of contract migration, sequenced after promotion**. **Old vectors
become unusable the moment `classification_revision` is set — not when
re-embedding finishes.** The window is "no evidence yet", never "stale evidence
still current".

Graph cleanup is deferred garbage collection and is **never a prerequisite for
legal promotion**.

## Legacy preservation

No legacy field is deleted, rewritten or normalised. Categories, upload ids,
clause rows, audit rows and empty project values all remain exactly as they are —
they are the migration's evidence and audit provenance.
