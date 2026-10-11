# Contract Master v1 — architecture overview

**Status: accepted.** This set states the CURRENT architecture. It is not a
decision diary; where a decision replaced an earlier one, only the current
decision appears here. The history lives in
[`DECISION-SUPERSESSION-INDEX.md`](contract_master_v1_decision_supersession_index.md) and in the
full decision trail, which is kept in the team design workspace and is
deliberately not published here: it states superseded claims as current at the
time they were written.

Companion documents:

| # | Document |
|---|---|
| 01 | [Domain, authority and persistence](contract_master_v1_01_domain_and_authority.md) |
| 02 | [Documents, classification and projection](contract_master_v1_02_document_classification_projection.md) |
| 03 | [Retrieval, evidence and provenance](contract_master_v1_03_retrieval_and_evidence.md) |
| 04 | [Permissions and RBAC](contract_master_v1_04_permissions_and_rbac.md) |
| 05 | [Upload and migration](contract_master_v1_05_upload_and_migration.md) |
| 06 | [Frontend semantics](contract_master_v1_06_frontend_semantics.md) |
| 07 | [Acceptance and release gates](contract_master_v1_07_acceptance_and_release_gates.md) |
| 08 | [Implementation debt](contract_master_v1_08_implementation_debt.md) |

---

## What Contract Master v1 is

A contract instrument — a GCC, an SCC, a Letter of Acceptance, an amendment — is
a **legal document owned by an organisation** that **governs one or more
contracts** for defined periods of time. Before Contract Master, the system had
uploaded files with filenames and no way to say *which instrument governs this
contract, on this date, and which one overrides which*.

Contract Master introduces exactly that, and nothing more: an authoritative
instrument record, an explicit temporal relationship to contracts, and a
guarantee that no derived artefact can substitute for either.

## The five rules everything else follows from

**1. Ownership is not reach.**
An organisation owns a `ContractDocument`. Ownership grants catalogue
visibility. It grants **no evidence reach** into any project or contract. Reach
comes only from an explicit applicability record naming
`(organization_id, contract_document_id, project_id, contract_id)`.

**2. Absence is never authority.**
`None`, `""`, a missing field, an expired session, a cleared dropdown and an
omitted request field are all *absences*. None of them may establish a legal
scope, a date, an applicability, or a type. Where the system needs a
categorical answer it carries a categorical discriminator.

**3. Nothing derived may authorise anything.**
`ContractClause` rows, Qdrant points, Falkor nodes, filenames, retrieval scores
and cached client state are **projections**. They may be used to find or rank
material. They may never decide whether material may be used, which instrument
governs, or what scope applies.

**4. Authority precedes bounding.**
Every candidate source must be constrained to the canonical eligible universe
**before** it applies its own limit. A filter applied after a `LIMIT` protects
the results but not the candidates, and a legitimate instrument starved out of
the window is indistinguishable from one that does not exist.

**5. Fail closed, and fail visibly.**
Ambiguity denies. Uncertainty denies. A failure that cannot be resolved
hard-fails rather than broadening; a source that merely failed to enrich marks
the result degraded rather than presenting it as complete. Zero applicable
evidence is a **valid answer**, never a reason to widen the search.

## Contract identity

```
Contract identity = (project_id, contract_id)
```

`contract_id = "primary"` is a **default value, not a uniqueness constraint**.
The same string exists in many projects and denotes different contracts. No
store, query, cache key, route or UI element may key on `contract_id` alone.

Contract remains **project-scoped in v1**: every contract belongs to exactly
one project, and contract identity is always the pair above.

## The authority chain

```
actor
 → tenant membership / generic RBAC
 → active Project + active Contract
 → ContractDocumentApplicability at the requested mode/date
 → ContractDocumentLegalEffect resolution (override / supersede)
 → ContractDocument
 → canonical Document
 → publication_policy / is_consumable        ← evaluated now, never snapshotted
 → DocumentVersion
 → authorised ContractClause projections
 → ranking / retrieval / consumer
 → ContractAnalysisProvenance recorded
```

Nothing at any level may self-authorise something above it.

## The canonical eligible set

One computation, four conjuncts, shared by every retrieval source:

```
eligible_document_ids =
      ContractDocuments applicable to (project_id, contract_id) at the query mode
    ∩ canonical Document positively resolvable
    ∩ not publication-blocked
    ∩ projection-current
```

It is a **positive set**, not a subtraction. This matters because the existing
`blocked_document_ids` helper deliberately fails open on an id it cannot
resolve — an orphaned point is not evidence that a document is blocked. A
positive list excludes the unresolvable structurally.

Because no candidate store carries `contract_id`, **this set is the sole carrier
of contract identity in retrieval**. It is load-bearing, not defence in depth.

## Three separated taxonomies

| Axis | Answers | Members |
|---|---|---|
| `ContractDocumentType` | what legal instrument is this? | 17, authoritative once resolved |
| `CategoryKey` | what subject does this clause concern? | 19, never an instrument |
| `relationship_role` | how does this document relate to that entity? | relational only; for contract documents, `supporting_document` |

Conflating them is the failure mode this architecture was designed against, and
the codebase already contains one live instance of it (see
[08](contract_master_v1_08_implementation_debt.md), CM08-FIND-01).

## What v1 deliberately excludes

Multi-project contract identity · a legal-effect editor UI · a general temporal
reasoning engine for multi-date claims · scope correction after promotion ·
materialised applicability intervals · organisation-scoped supporting-document
links.

Each is recorded with its trigger condition; none is a gap discovered late.

## Reading order

Start here, then [01](contract_master_v1_01_domain_and_authority.md) for the model, then
[03](contract_master_v1_03_retrieval_and_evidence.md) for the part most likely to be got wrong in
implementation. [08](contract_master_v1_08_implementation_debt.md) is the gap between this
architecture and what the repository does today — read it before estimating
anything.
