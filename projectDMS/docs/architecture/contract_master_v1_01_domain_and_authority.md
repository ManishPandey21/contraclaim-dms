# Contract Master v1 — domain, authority and persistence

Current state. See [00](contract_master_v1_00_overview.md) for the governing rules.

## Cardinalities

```
Organisation 1 ──── * Project
Project      1 ──── * Contract            identity = (project_id, contract_id)
Organisation 1 ──── * ContractDocument
ContractDocument 1 ── 1 Document          (canonical, 1:1)
Document     1 ──── * DocumentVersion
ContractDocument * ── * Contract          via ContractDocumentApplicability
ContractDocument 1 ── * ContractDocumentClassificationFact
ContractDocumentApplicability 1 ── * ApplicabilityLifecycleEvent
ContractDocument * ── * ContractDocument  via ContractDocumentLegalEffect
Document     1 ──── * ContractSection 1 ── * ContractClause   (projections)
```

An organisation reaches contracts only through its projects.

## Entities

| Entity | Identity | Owner | Authority source | Temporal | Deletion |
|---|---|---|---|---|---|
| **Contract** | `(project_id, contract_id)` | Project | project scope | none | out of scope v1 |
| **ContractDocument** | `contract_document_id` | **Organisation** | itself, metadata only — **never authorises bytes** | none directly | metadata retained even when the canonical Document is blocked |
| **ContractDocumentType** | enum value | domain | the enum, 17 members | none | n/a |
| **ContractDocumentClassificationFact** | fact id | ContractDocument | actor + basis | `asserted_at` | never deleted |
| **ContractDocumentApplicability** | `(org, contract_document, project, contract)` + temporal identity | ContractDocument | its lifecycle events | half-open interval | never deleted |
| **ApplicabilityLifecycleEvent** | event id | Applicability | **itself — the legal fact** | `effective_at` | never deleted |
| **ContractDocumentLegalEffect** | effect id | ContractDocument pair | itself | `effective_from` | never deleted |
| **Document** | `document_id` | Organisation/Project | `publication_policy` / `is_consumable` | none | existing rules |
| **ContractSection / ContractClause** | row id | Document | **none — projection** | none | rebuildable |
| **ContractAnalysisProvenance** | `run_id` | consuming run | none — a record | `retrieved_at` | retained |

## Canonical instrument identity

**`ContractDocument.document_id` is the canonical 1:1 instrument identity.**
It is a direct field on the ContractDocument, not a link row.

Generic `entity_document_links` for a contract-document target carry exactly one
role: **`supporting_document`**. Canonical instrument identity is carried by
`ContractDocument.document_id` alone, which makes any second identity role
redundant — and a second representation of the same fact is a second thing to
keep in sync.

## Scope

`ContractScopeLevel ∈ {organization, project}` — an **explicit discriminator**,
never inferred from `None`, `""`, a missing field, a role name, or an active UI
selection.

- **`organization`** — the ownership record carries no `project_id` at all.
  Zero applicability rows is a legal, final state: **catalogued / unassigned**.
  It never means "all projects".
- **`project`** — at least one applicability row is required at authoritative
  creation. Every applicability row carries the **same `project_id`**; further
  contracts *within that project* may be added later.

The project anchor is a **stored structural invariant**, set at upload,
immutable after promotion. It is never derived from the first applicability —
deriving it would make an instrument's ownership time-varying while link
identity assumes it fixed.

Role `scope` metadata exists in the RBAC seed data but is **not read at
runtime** and defaults fail-open when missing. Contract Master must never depend
on it; organisation tier is expressed as an explicit capability
(see [04](contract_master_v1_04_permissions_and_rbac.md)).

## Temporal model

Half-open interval: `effective_from <= event_date < effective_to`;
`effective_to = null` is open-ended.

**`effective_from` absent means UNKNOWN**, never "since forever". A start date is
never synthesised from a document's `createdAt`, an upload timestamp, a contract
start, an audit row, or a first clause date.

**CURRENT** iff an `APPLIED` event exists, no subsequent `WITHDRAWN` or
`SUPERSEDED` applies, and no known future `effective_from` excludes it. An
unknown start does not disqualify a current-state answer.

**HISTORICAL(D)** requires known temporal evidence. An unknown `effective_from`
matches no D — historical questions fail closed rather than guessing.

Two contradictory current states → **fail closed**.

Legal effective time and recorded time are distinct. Replay uses legal effective
time; a newest-created event never automatically wins.

## Persistence

**Event stream only. No materialised current-interval cache in v1.**

The authoritative record is the append-only `ApplicabilityLifecycleEvent`
stream. `effective_to` is a **derived projection**, never stored as the primary
fact.

The applicability aggregate — `(organization_id, contract_document_id,
project_id, contract_id)` — is stable, so replay is bounded to roughly three
events per aggregate. The aggregate supplies the index; the events supply the
authority. A cache would add an invalidation problem to buy an optimisation
nothing has yet demonstrated a need for.

Overlap is guarded **transactionally**, not by a unique index — the constraint is
temporal, and an index cannot express a half-open interval overlap.

**No TTL on legal history.** The only TTL in the contract path is on upload
sessions, which carry no legal fact.

Materialisation is a **decided deferral with an explicit trigger**: measured
resolver p95 over budget. It graduates to a ticket only if that fires.

## Query modes

```
ApplicabilityQueryMode = Browse | CurrentState | Historical(event_date)
```

A closed sum type. **No fallback in either direction** — `Historical` never
silently becomes `CurrentState`, `CurrentState` never answers a historical
question, and there is no implicit "today". `Historical` without an `event_date`
must be unconstructable, not merely rejected at runtime.

## Result types

`BrowseResult` and `ApplicableInstrument` are **structurally different types**.

Browse answers *"what exists in the catalogue?"*. Evidence answers *"what governs
this contract?"*. A `BrowseResult` may never enter an evidence consumer, and the
separation is enforced by the type, not by a runtime check that a dictionary can
slip past.

## Legal precedence

Precedence comes from, and only from:

```
ContractDocumentApplicability
  + ContractDocumentLegalEffect  (OVERRIDE | SUPERSEDE)
  + effective date
  + scope of effect              (WHOLE_DOCUMENT | CLAUSE_REFERENCES)
  + current Document authority
```

**Never** from: a filename, a stored `section_type`, a Falkor `priority`, a
Qdrant score, a graph score, a document type alone, a clause row id, or
retrieval rank.

Retrieval score answers *"how relevant is this?"*. Legal effect answers *"which
governs?"*. They are different questions and conflating them is the most
consequential error available in this domain.

When two applicable clauses have **no legal-effect record between them**, the
consumer surfaces `legal_resolution: UNRESOLVED` with both instruments and
asserts neither. Fail closed on the assertion, not on the answer.

## Promotion states

```
RECONCILIATION → AUTHORITATIVE → EVIDENCE_CAPABLE      (QUARANTINED terminal)
```

Scope and type classification progress **independently**, and both are retained
as they resolve — but **promotion is holistic**. A resolved axis is progress,
never partial legalisation.

`EVIDENCE_CAPABLE` is **not a persisted migration status**. It is the query-time
conjunction of applicability, current Document authority, projection currency
and actor authorisation. Persisting it would freeze a judgement whose inputs
change independently.

## Two reproducibility questions, deliberately separate

| | Legal reproducibility | Analysis reproducibility |
|---|---|---|
| Question | which instrument applied on D? | what did that run actually consume? |
| Source | applicability + legal-effect history | `ContractAnalysisProvenance` |
| Depends on `DocumentVersion` | **no** | **yes — records it** |
| Answerable when content is blocked | **yes** (the fact survives) | yes (the record survives); content still denied |
