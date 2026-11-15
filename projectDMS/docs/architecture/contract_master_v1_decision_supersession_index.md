# Contract Master v1 — decision supersession index

This is where historical correction context lives. **Documents 00–08 state only
current decisions.** If you are looking for "we used to think X", it is here.

Each row: what was once asserted, where it came from, what is true now, what
replaced it, how that was established, and the current status.

## A. Corrections to earlier Contract Master decisions

| # | Superseded assertion | Origin | **Current assertion** | Superseded by | Verification basis | Status |
|---|---|---|---|---|---|---|
| S-01 | `relationship_role` for contract documents includes `primary_document` | ADR 0004; CM-02 | **`ContractDocument.document_id` is the canonical 1:1 identity; the only role is `supporting_document`** | CM-03, confirmed CM-05 | canonical field makes the role redundant; verified no such role exists anywhere in the backend | CLOSED |
| S-02 | applicability persistence is event stream **plus** a materialised current-interval cache | early grill framing | **event stream only in v1; `effective_to` derived, never stored** | CM-03 | the aggregate is stable, so replay is ~3 events; a cache buys an unproven optimisation and costs an invalidation problem | CLOSED |
| S-03 | keep `CONTRACT_APPLICABILITY_MANAGE` **out** of the client DMS permission list so Project Admin cannot inherit it (recorded as **P16**) | CM-02 | **both new permissions stay in the list; the bulk role merge is narrowed by an `ORG_TIER_ONLY_PERMISSIONS` exclusion applied to `projectadmin` only** | CM-14 (CM14-FIND-01) | the list is simultaneously the permission catalogue, the entitlement scope set and the bulk grant; omission would leave the permission unseeded and would bypass the subscription gate | CLOSED |
| S-04 | `CONTRACT_MASTER_VIEW` is sufficient to protect the organisation catalogue | CM-02 framing | **it is bulk-granted to `projectadmin` and cannot isolate; catalogue access requires `CONTRACT_CATALOGUE_BROWSE`** | CM-04 | read from the permission list and role seeding | CLOSED |
| S-05 | role `scope` metadata is an authorisation boundary | pre-programme assumption | **written but never read at runtime; defaults fail-open; Contract Master must not depend on it** | CM-04 | traced the seed field to the absence of any runtime reader | CLOSED |
| S-06 | an organisation-scoped instrument with exactly one applicability may use that project for generic supporting links | CM-05 native framing | **never — at any applicability count** | CM-05 | borrowing makes the target's project time-varying while link identity assumes it fixed; the generic service already refuses project-less targets | CLOSED |
| S-07 | publication authority runs **after** fusion, ranking, count and pagination | early programme claim | **it runs before all four; the remaining defect is candidate starvation, which happens earlier still** | CM-06, re-verified CM-14 | read the ordering directly, twice, in separate sessions | CLOSED |
| S-08 | filename-derived graph `priority` is a **live HIGH authority leak** | Key Date cycle carry-over | **persisted but unread by contract retrieval — no ordering, no filtering, no score contribution, discarded on read. LOW/MEDIUM misleading state and a future footgun** | CM-11 | traced every read path; the score derives from relation topology alone | CLOSED |
| S-09 | an empty eligible list always produces a **fully unfiltered global** vector search | CM-07 | **the builder drops empty conditions; the resulting breadth depends on which filters survive — organisation-wide for a scoped caller, genuinely global only where the tenant filter may also be absent. The short-circuit rule is unchanged** | CM-14 (CM14-FIND-03) | read the filter builder together with the tenant-scope guard | CLOSED |
| S-10 | that same empty-list hazard applies to the lexical path | inferred from S-09 | **it does not — the document store treats an empty membership set as match-none. The short-circuit stands for parity and cost, not safety** | CM-15 (CM15-FIND-03) | store semantics differ from the vector client's builder | CLOSED |
| S-11 | the resolver writes analysis provenance | early framing | **the consumer writes it; the resolver carries the identifiers** | CM-10 | only the consumer knows what was used after ranking, truncation and prompt assembly; resolver-written provenance records a superset | CLOSED |
| S-12 | provenance failure is accepted post-commit, as in the legacy backfill | backfill precedent | **provenance is a precondition of the accepted state; a persisted artefact cannot reach accepted/complete without it** | CM-10 | the deliverable here has a lifecycle the backfill's did not, which makes the inversion possible | CLOSED |
| S-13 | a retained audit null establishes confirmed organisation scope | brief framing, M09-02 | **it is an ambiguity hint only; confirmed organisation scope requires operator adjudication** | CM-09 (CM09-FIND-01) | the upload path collapses null and empty before any durable write, so the surviving null is an absence one level removed | CLOSED |
| S-14 | the contract retrieval stack simply forgets to filter clause currency and AI authorisation | CM-08 (CM08-FIND-04) | **it queries a different collection, on which those fields do not exist** | CM-15 (CM15-FIND-01) | traced the collection accessor used by both candidate generation and hydration | REFINED |
| S-15 | migration should register as a module in the accepted legacy-backfill registry | CM-09 native framing | **borrow the pattern and the generic primitives; do not register** | CM-09 | that service derives scope from a canonical target that does not exist yet, and deriving scope is the deliverable | CLOSED |
| S-16 | S-08 settled the filename-derived graph `section_type`/`priority` state completely, so nothing about that state can be a precondition for anything | implicit reading of S-08 after CM-11 | **Two different questions, and only one of them was ever answered. (i) RUNTIME AUTHORITY — unchanged: LOW/MEDIUM, NON-GATING. No `ORDER BY` on `priority`/`section_type`, no filtering, no score contribution; the values are discarded on the evidence path and graph relevance derives from relation topology alone. (ii) GRAPH CERTIFICATION — separate: residual historical nodes still carrying that derived state may be an evidence/cleanup precondition for the applicable graph gate, because certification must account for residual derived state. That is CERTIFICATION HYGIENE / STATE ACCOUNTING, not runtime authority. THIS DOES NOT RESTORE THE RETIRED HIGH SEVERITY.** | handoff-hardening phase (Q4), 2026-08-30; see `GRAPH-GATES.md` U8 | S-08's read-path tracing re-read and unchanged. The certification half is a *scope* statement about what gate evidence must account for, not a new runtime finding; which gate owns the accounting is RECONSTRUCTED — CONFIRM REQUIRED (G29 and G32 both plausible) | CLARIFIED |

**On S-16.** A severity finding and a certification precondition are not the same object. S-08 retired the HIGH by tracing every read path, and that stands: no consumer of contract evidence reads the field. S-16 says only that a graph gate whose subject is node state cannot certify *around* state it has not accounted for. If the accounting concludes the residue is inert and may stay, that is a valid outcome — it is the absence of an answer, not the answer, that blocks a gate.

## B. Assertions verified absent

Checked during consolidation and confirmed not present as active claims anywhere
in the current set:

`primary_document` as a live role · "materialised cache" as v1 persistence ·
"authority after fusion" · "Falkor priority is HIGH" · "audit null proves
organisation scope" · "resolver writes provenance" · "uploaded means
ContractDocument exists" · "completed means evidence ready" · "all completed
files in project" as an evidence mode · "blank project means organisation".

Where these strings still appear in the unpublished Wayfinder trail and in
the pre-consolidation design documents, they appear **inside their own correction
context** — which is the trail working as intended. Two trail artefacts carry an
amendment marker added during consolidation:

- **ADR 0004** listed `primary_document` in a frozen initial role set. Amended to
  point at S-01.
- **The map's out-of-scope section** described "fixing the Falkor priority HIGH".
  Amended to the corrected severity per S-08.

## C. How to read a conflict

If a statement in the trail contradicts documents 00–08, **00–08 wins**. The
precedence that produced them:

1. current repository source and runtime evidence;
2. later accepted Wayfinder decisions;
3. adversarial-acceptance corrections;
4. accepted ADRs **as amended** by later decisions;
5. earlier ticket text;
6. historical assumptions.

Contradictory assertions were never averaged or merged. In every case above, the
later mechanically verified decision replaced the earlier one outright.
