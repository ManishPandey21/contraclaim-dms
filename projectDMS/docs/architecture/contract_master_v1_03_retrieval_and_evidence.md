# Contract Master v1 — retrieval, evidence, provenance and degraded mode

Current state. See [00](contract_master_v1_00_overview.md) for the governing rules.
This is the document most likely to be got wrong in implementation.

## The unified retrieval invariant

> **LEXICAL, VECTOR and GRAPH operate over the same canonical
> `eligible_document_ids` universe, applied before each source's bounded
> candidate generation. Only source-specific relevance may differ.**

If the three sources search different populations, reciprocal-rank fusion
combines three rankings over three universes and the result is meaningless even
when every individual source is correctly contained.

## The eligible set

```
eligible_document_ids =
      ContractDocuments applicable to (project_id, contract_id) at the query mode
    ∩ canonical Document positively resolvable
    ∩ not publication-blocked
    ∩ projection-current      (projection_status == CURRENT
                               AND projection_revision == classification_revision)
```

Computed **once** per query, applied before **every** source's limit.

### Why positive, not subtractive

The shared publication filter deliberately **fails open on an id it cannot
resolve**: an id absent from the document collection is treated as an orphaned
point, not as a blocked document. That is correct for generic search — an
orphaned vector is not evidence that anything is blocked — and wrong for legal
evidence, where an unresolvable candidate must never be served.

A positive list closes this structurally: an unresolvable id was never *in* the
set. The publication filter remains as defence in depth; it is never the
boundary.

**The publication filter must never be the sole gate on the contract evidence
path.** Reusing only the existing filter is the obvious shortcut and it inherits
fail-open-on-missing for legal evidence.

### Why the fourth conjunct lives here

Projection currency is a **document-level** fact. Folding it into eligibility
means it applies to lexical, vector and graph identically with zero per-store
work, and it requires no schema change to any derived store — so it does not wait
on any migration. Denormalising the generation onto every chunk was rejected: it
recreates the per-record invalidation problem that removing the type copy
eliminated.

### Contract identity lives here too

**No candidate store carries `contract_id`** — not the lexical store, not the
vector payload, not the graph query. The eligible set is therefore the **sole
carrier of contract identity in retrieval**, which makes it load-bearing rather
than defence in depth.

`contract_id = "primary"` in project A and project B resolve to **different
eligible sets**, because applicability is keyed on the pair.

## Authority ordering versus candidate capacity

Two distinct concerns that must not be conflated:

**Authority ordering — correct.** The publication filter runs *before* fusion,
ranking, total count and pagination. Results are never contaminated by blocked
documents, and the count never includes them.

**Candidate capacity — the real gap.** Each source fetches its candidates under a
bounded limit *before* applicability exists. N high-scoring non-applicable
candidates can therefore fill the window and starve an applicable one that would
have ranked just outside it. The applicability constraint must be applied
**inside each source's own query, before that query's limit**.

## Per-source contract

### Lexical

The eligible-document predicate joins the query's first match stage, which
already precedes the limit — so no pipeline reordering is required. Composition
with any caller-supplied document filter is **intersection, never replacement**.

An empty eligible set **short-circuits**: no query is issued. (Mongo's `$in: []`
is correctly match-none, so this is a parity and cost decision rather than a
safety fix — but all three sources behave identically so no reader has to know
which stores are safe by accident.)

The organisation constraint is **mandatory in evidence mode**. Generic search
omits it for global roles; evidence must not.

### Vector

Canonical eligibility resolves in the document store first, then the eligible
`document_id` list becomes a payload prefilter applied **before** the candidate
limit. Applicability is **never denormalised into the vector payload** — the
payload has no contract identity and no update path on withdrawal or
supersession, and stale applicability is a wrong legal answer rather than a slow
one.

An empty eligible set **short-circuits before any call is issued**. This one *is*
a safety rule: the filter builder drops empty list conditions, so depending on
which other filters survive, an empty list broadens the search — to
organisation-wide for a scoped caller, and genuinely globally on the
admin/global path where the tenant filter may also be absent.

Payload fields are **hints only**: they may narrow candidates, never authorise
them. Stale payload metadata can produce a false candidate; it can never produce
a false authorisation, because hydration and eligibility are both canonical.

### Graph

The eligible-document constraint becomes a predicate evaluated **before the
graph query's limit**. An empty eligible set short-circuits rather than building
an empty membership list.

**Every graph field is non-authoritative** — `section_type`, `priority`, title,
text, page, active flags, and any project or contract property on a node. The
graph contributes **topology-derived relevance** and nothing else.

**Graph priority may never influence legal precedence, and may not influence
relevance ranking either** in v1: it is filename-derived, so it is not a
relevance signal — it is noise with a misleading name. Topology already supplies
the graph's legitimate contribution.

Disabling the graph entirely must not change which instruments are applicable,
which documents are consumable, or which instrument overrides another. It **may**
change relevance ordering.

## Join-back

Canonical join-back key: **`document_id` + `clause_number`** (+ clause start
position where present). Shared by all sources. Never a filename,
`section_type`, `priority`, or `contract_id` alone.

Every candidate is rehydrated from canonical records before it reaches a
consumer. A raw vector chunk, graph candidate, lexical row or `BrowseResult`
never enters prompt assembly.

## Failure semantics

| Class | Behaviour |
|---|---|
| scope resolver failure | **HARD FAIL** |
| ambiguous or conflicting applicability | **HARD FAIL** |
| missing `event_date` in `Historical` | **HARD FAIL** |
| publication-authority failure for required content | **HARD FAIL** |
| eligible set cannot be constructed | **HARD FAIL** |
| the filter cannot represent the set | **HARD FAIL** — never silently drop the predicate |
| a retrieval source is unavailable | **DEGRADED**, explicitly marked, only if the remaining candidates are canonically authorised |
| zero applicable instruments | **VALID EMPTY** — never a reason to broaden |

The split is: *who may be searched* is authority; *whether one source answered*
is enrichment. No authority failure may degrade into a broader search, and no
degraded result may present as complete.

**Silent degradation is prohibited on the evidence path.** All three sources
currently fail silently to an empty list and let the search continue — acceptable
for generic search, unacceptable for a legal answer presented as complete.

## The evidence consumer contract

Every consumer must:

1. **state a query mode explicitly** — `CurrentState` or `Historical(event_date)`,
   never `Browse`, no fallback in either direction, no implicit today;
2. **accept only `ApplicableInstrument`** (or an evidence type derived from it) —
   a raw `document_id`, clause row, vector chunk, graph candidate or
   `BrowseResult` is never sufficient;
3. **take legal precedence from canonical state**, never from retrieval rank;
4. **write provenance** under the rules below;
5. **propagate degraded state** into whatever it persists.

Precedence is injected by the resolver as canonical state on the evidence set. It
is **never inferred by the model** from document labels, filenames or rank order.

### Blocked but historically applicable

Two separable facts, both representable at once:

- *legal applicability* — instrument X applied to (P, C) on D — **known**, from
  immutable history;
- *content* — currently denied.

The consumer shows the instrument and its applicability basis, and reports the
content unavailable. It must **not** silently drop the instrument (pretending it
never applied) and must **not** expose the bytes.

### Cached evidence is not an authority token

A previously resolved evidence object may not authorise a new run. A new run
re-resolves: applicability may have been withdrawn, a legal effect added, the
document blocked. Stored provenance remains an audit record of the earlier run —
which is precisely why it must not be replayed as permission.

## Provenance

**The consumer writes provenance, not the resolver.**

The resolver knows what it *resolved*; only the consumer knows what it *used* —
after ranking, truncation, top-K selection and prompt assembly. Resolver-written
provenance would record a **superset** of the truth, which is a false record
wearing the shape of an audit trail.

The resolver's narrower obligation: every `ApplicableInstrument` carries the
identifiers the consumer needs — `contract_document_id`, `document_id`, the
resolved `document_version_id`, the applicability event/revision basis, the
legal-effect references, and the `classification_revision` in force. A consumer
must never reconstruct those.

### Provenance is a precondition of the accepted state

**An artefact does not reach accepted/complete without its provenance.** If the
write fails, the artefact stays non-accepted and the failure surfaces.

Split by whether the output persists:

| Consumer | Without provenance |
|---|---|
| interactive Q&A (displayed, not persisted) | **may proceed**, marked `provenance: unrecorded`; may not be exported, saved, or cited as evidence |
| claim drafting | **no** |
| arbitration analysis | **no** |
| evidence bundle | **no** — a bundle whose contents cannot be reconstructed is not a bundle |
| raw clause retrieval | n/a — returns candidates, not resolved evidence |

A transient answer on screen is a conversation; a persisted artefact is a record,
and a record without provenance is unreproducible by definition.

### Mandatory fields

`analysis_run_id` · `consumer_type` · `organization_id` · `project_id` ·
`contract_id` · `query_mode` · `event_date` when historical ·
`contract_document_id` · `document_id` · **`document_version_id`** ·
**`classification_revision`** · applicability event/revision basis ·
legal-effect references · `retrieval_status` + degraded sources ·
`retrieved_at`. No document bytes.

## Two authority helpers

The codebase has two: a shared batch filter used by most consumers, and a
per-row resolver used directly by arbitration. Their **rules agree** — both apply
the quarantine and processing-status axes. Their **failure directions differ**:
the per-row resolver fails closed on an unresolvable document, the batch filter
deliberately does not.

This divergence is safe under this architecture **only because** contract
evidence is gated by the positive eligible set, which excludes unresolvable
candidates structurally. That safety is conditional and must be pinned by test,
not assumed.
