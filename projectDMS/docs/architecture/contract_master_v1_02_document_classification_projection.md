# Contract Master v1 — documents, relationships, classification and projection

Current state. See [00](contract_master_v1_00_overview.md) for the governing rules.

## The canonical Document is unchanged

Contract Master adds no field to the generic `Document` model and changes no
generic document semantics.

`Document.project_id` continues to hold `""` where no project applies. **It is
not contract scope authority and must never be read as such.** Contract scope
lives in the contract domain, on `ContractDocument.scope_level` and its anchor.

Consequence to keep in mind: an organisation-scoped instrument's canonical
Document still carries `project_id=""`, so it remains outside project-scoped
generic document listings and outside generic relationship targeting. That is
existing behaviour, neither improved nor worsened here.

## Relationships

The **contract document adapter serves project-scoped ContractDocuments only.**

Generic `DocumentRelationshipService` requires both the target and the supporting
document to carry an explicit project, and refuses otherwise. An
organisation-scoped ContractDocument therefore fails closed as a relationship
target with **no change to the generic service**.

**An organisation-scoped instrument is never a generic relationship target — at
any applicability count, including exactly one.** Borrowing the single current
applicability's project would make the target's project *time-varying* while link
identity assumes it fixed: withdraw that applicability and the link's ownership
becomes undefined.

Adapter properties: `supports_freeze = False`; roles `{supporting_document}`.

Removing a supporting relationship **never** withdraws applicability, alters a
legal effect, or clears `ContractDocument.document_id`.

**Application-specific supporting evidence** — attaching support to one
applicability rather than to the instrument — is the right future path for
multi-project organisation instruments. No v1 case demonstrates the need.

## Classification

**One authoritative source: `ContractDocument.contract_document_type`**, set by
an append-only classification fact.

```
TYPE_SUGGESTED  →  human confirmation  →  TYPE_RESOLVED
```

A correction **appends a new fact**. Nothing is overwritten, and nothing is
deleted.

- `other_contractual_document` is an **affirmative classification**, never the
  fallback for unknown or unrecognised.
- `UNKNOWN` is not an enum member. An unresolved type stays visibly unresolved.
- Filename heuristics are **suggestion input only** — permitted at upload and at
  migration, forbidden as a runtime override of a confirmed classification.
  Renaming a file appends no classification fact and therefore changes nothing.

Never authoritative: `ContractClause.document_type`, filename,
`contract_categories`, appraisal inference, Falkor `section_type`/`priority`,
any Qdrant payload field.

**Classification does not touch applicability or legal effect.** Correcting a
type from GCC to SCC creates no `APPLIED`, `WITHDRAWN`, `OVERRIDE` or
`SUPERSEDE`. Classification names the instrument *kind*; legal effect is a
separate canonical relation. Reprojection may never synthesise a legal relation.

## Projection generation

```
classification_revision   monotonic integer on ContractDocument, +1 per TYPE_RESOLVED
projection_revision       the revision a completed reprojection was built for
projection_status         PENDING | IN_PROGRESS | CURRENT | FAILED
```

`classification_revision` is deliberately **an integer, not a timestamp** (so no
consumer can select "newest") and **not filename-derived** (so no mutable value
can act as identity).

Every derived record carries `source_classification_revision`.

### The staleness rule

```
projection-current  ⟺  projection_status == CURRENT
                   AND  projection_revision == classification_revision
```

**Equality — never `>=`, never newest-wins.** A projection becomes unusable the
instant revision N+1 is confirmed: before any worker runs, with no invalidation
write anywhere. Staleness is a *comparison*, not a state someone must remember
to set.

A stale projection is **not degradable**. Classification is a legal-resolution
input, so it belongs to the hard-fail class.

## Reprojection

**Queued, asynchronous. The correction commits immediately; evidence capability
is gated by the comparison above.**

Synchronous reprojection was rejected on a structural ground, not a performance
one: reprojection re-runs the clause pipeline — page load, chunking, quality
derivation, embedding, vector write, graph sync — so a synchronous design makes
a **classification correction fail when the embedder is down**. A derived store
would then hold a veto over an authoritative legal fact, inverting the authority
direction this architecture exists to establish. An operator must always be able
to correct a misclassification; what is withheld is *evidence*, not the
correction.

Lazy/deferred reprojection was rejected because it puts an unbounded write on
the evidence path.

**The clause set in between is unchanged and complete**, tagged with revision N
while the document is at N+1. Nothing is deleted or emptied. Rows stay fully
readable for the clause index and operator review, and are excluded from evidence
by the comparison alone.

### Fencing

A worker reads `classification_revision = N` at start, stamps every row it
produces, and commits under a conditional guard on
`classification_revision == N`. If the value has advanced it **discards its
output and exits without writing**.

A late revision-N worker therefore cannot overwrite a revision-N+1 projection,
and `projection_revision` never regresses. An archived or deleted document fails
the same guard.

Retries converge because the clause identity contains **no classification
component** — a property that must be preserved, since adding one would fork the
clause set on every correction and stop retries converging.

### Failure

Reprojection failure leaves `classification_revision` untouched. **Legal history
never rolls back because a derived projection failed.** `projection_status`
becomes `FAILED`, `projection_revision` is unchanged, and the evidence path
hard-fails rather than silently using the prior generation.

## Derived stores

### Clause rows

**A clause row does not carry a copy of the instrument type.** Consumers needing
the instrument kind join to the `ContractDocument`. A derived copy of an
authoritative value is exactly what creates the staleness problem; removing the
copy removes the class of bug rather than managing it.

Clause rows carry `source_classification_revision`. Their `project_id` and
`contract_id` are **ingest provenance, never authorisation** — `(project_id,
contract_id)` for evidence comes from the eligible set at query time.

After a correction, old rows are neither deleted nor rewritten in place: they are
stale by comparison, superseded through the existing current/superseded fields,
and regenerated at the new revision.

Legacy clause rows are **historical**, not current projections of a promoted
instrument. They may seed reconciliation *evidence*; they never seed authority.

### Vectors

**Instrument classification is removed from the embedding input.**

Classification currently sits *inside* the embedded text, which means a
correction would require re-embedding every chunk — an expensive operation whose
failure would block evidence. Removing it makes a correction payload-only or
nothing at all.

It is also the right modelling call independently: instrument kind is a
document-level *legal* fact, not a semantic property of clause text. It belongs
in the eligible-set prefilter, not in similarity space. Embedding it lets a legal
classification perturb semantic ranking — the relevance/precedence conflation
this architecture forbids.

The payload carries `source_classification_revision` as a **negative hint only**:
it may cause a candidate to be dropped, never to be authorised.

Removing a field from the embedded string is an embedding-space change requiring
a version bump and a one-time re-embed — sequenced in
[05](contract_master_v1_05_upload_and_migration.md), not a prerequisite for promotion.

### Graph

New projections **stop writing filename-derived `section_type` and `priority`**,
and nothing authoritative-looking replaces them. Re-deriving them from the
authoritative type was rejected: a correct-looking classification inside a store
that is defined as non-authoritative is an invitation to the future `ORDER BY
priority` that the acceptance gate exists to forbid. Absence is safer than a
correct value in the wrong place.

Graph clause-node identity **must not contain the classification**, or a
correction strands a generation of nodes instead of updating them.

Existing nodes carrying filename-derived values are contained as
non-authoritative and are cleaned up by a separate effort. **Legal promotion
never depends on graph availability.**
