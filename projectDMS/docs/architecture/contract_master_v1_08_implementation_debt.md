# Contract Master v1 — implementation debt

**This document describes what the repository does TODAY.** It is the gap between
the current codebase and the target architecture in documents 00–06. Nothing here
is a target-state statement; nothing in 00–06 describes current behaviour.

Read this before estimating any implementation work.

Every entry was mechanically verified against source during the design
programme.

---

## Nine defects worth knowing before anything else

**A. Contract Q&A gates legal answers on ingestion status.** The Q&A surface
scopes a grounded answer by "completed" upload status and offers "all completed
files in project". It has **no contract identity, no applicability, no
publication authority, no projection currency and no query mode**. The page
invites a legal question and answers it from an ingestion flag.

**B. All three retrieval sources degrade silently.** Lexical, vector and graph
each catch broadly, log, and return an empty list while the search continues. A
total retrieval outage produces a confident empty answer.

**C. Candidate starvation precedes publication filtering.** Authority ordering is
correct — the publication filter runs before fusion, ranking, count and
pagination — but every source fetches under its own bound first, so blocked or
non-applicable candidates consume the window. A reviewer reproduced six blocked
clauses plus one clean governing clause at a page size of six, yielding a total
count of seven and **zero rows in the drafting prompt**, with no warning and no
degraded marking.

**D. The contract lexical path reads the legacy chunk store**, while the sibling
retrieval service reads the structured clause store and calls the former a
fallback in its own docstring. Two contract retrieval stacks over two different
collections.

**E. No candidate store carries contract identity.** Not the lexical store, not
the vector payload, not the graph query. This is why the positive eligible set is
load-bearing rather than defence in depth.

**F. The clause `document_type` axis collision is live.** The field is populated
from the clause-subject taxonomy, or from a generic literal — it can **never**
hold an instrument value. Three consumers branch on instrument values that
cannot occur, so all three are silently dead: a base-type exclusion that never
fires, a modifier list that is always empty, and a "base wording" selection that
always falls through to the first row. Modification links are written with an
assumed base type regardless of the actual one. Contained only by being
write-only — nothing reads them back.

**G. Classification participates in the embedding text.** It sits *inside* the
embedded string, not beside it, so a correction cannot be repaired by a payload
update.

**H. Graph clause-node identity embeds the type.** A correction creates a second
node under a new id and strands the first, accumulating a generation of ghosts
per correction.

**I. Upload collapses scope intent across nine layers.** From the browser to the
document row, every layer normalises a value that was never able to carry the
question. None of the nine is a bug in isolation; the defect is that no layer
carried the discriminator.

---

## Normalised debt register

| ID | Current behaviour | Target | Severity | Gating | Area | Tracer |
|---|---|---|---|---|---|---|
| **DEBT-01** | clause `document_type` holds a subject-taxonomy value or a generic literal; three consumers branch on instrument values that cannot occur | clause rows carry no type copy; consumers join to the instrument record; modification semantics come from canonical legal effect | MEDIUM | no | clause pipeline, letter drafting | C08-07 |
| **DEBT-02** | classification is embedded inside the semantic text | removed from embedding input; a correction becomes payload-only | MEDIUM | no | embedding, migration | C08-05 |
| **DEBT-03** | graph clause-node identity embeds the type | identity excludes classification | MEDIUM | no | clause graph | C08-06 |
| **DEBT-04** | the two contract retrieval stacks read different stores, so the contract path applies neither currency nor AI-authorisation filters | contract evidence applies both; long term, one store | MEDIUM | **yes** | contract retrieval | C08-11 |
| **DEBT-05** | clause scope validation demands project and contract, which an organisation-owned instrument has neither of | clause projection is document-scoped; clause project/contract are ingest provenance | MEDIUM | no | clause storage | — |
| **DEBT-06** | no positive organisation-scope evidence exists in the legacy corpus | organisation scope is operator-adjudicated | MEDIUM | **yes** | migration | M09-02 |
| **DEBT-07** | the creation audit write is best-effort and swallowed, so an absent row proves nothing | audit is a positive-only hint | MEDIUM | no | migration | M09-02b |
| **DEBT-08** | the strongest project signal lives on a TTL'd session | captured at inventory, never re-read at promotion | MEDIUM | no | migration | M09-22 |
| **DEBT-09** | organisation-scope upload is authorised by a **role-name test**, and an absent project is a side effect of three unrelated situations rather than a decision | capability at resource scope; explicit discriminator | MEDIUM | **yes** | upload, RBAC | U12-05 |
| **DEBT-10** | the two client upload paths disagree on the wire form of "no project" — one sends empty, one omits | one discriminated union, identical on both paths | MEDIUM | no | client API | U12-01 |
| **DEBT-11** | Q&A gates evidence on ingestion status and has no contract identity or mode | project + contract + mode; canonical eligible set | MEDIUM (HIGH as user-facing) | **yes** | frontend, Q&A | F13-09, F13-10 |
| **DEBT-12** | ingestion "completed" renders as a success badge | success styling only for evidence-ready | MEDIUM | no | frontend | F13-07 |
| **DEBT-13** | project selection is sticky client state whose setter is guarded, so clearing it never clears the stored value | client state may restore a selector value, never a discriminator | MEDIUM | no | frontend | F13-02 |
| **DEBT-14** | lexical candidate generation has no mandatory organisation predicate for global roles | evidence mode always constrains organisation | MEDIUM | **yes** | contract retrieval | L15-03 |
| **DEBT-15** | all three retrieval sources degrade silently to empty | evidence hard-fails or is explicitly marked degraded | MEDIUM | **yes** | contract retrieval | — |
| **DEBT-16** | blocked/non-applicable candidates consume the bounded window before authority applies | eligible-set predicate inside each source's query, before its limit | MEDIUM | **yes** | contract retrieval | L15-01 |
| **DEBT-17** | filename-derived graph `section_type`/`priority` are persisted but unread by contract retrieval — no ordering, no filtering, no score contribution, discarded on read | stop writing them; existing nodes cleaned up separately | LOW/MEDIUM | no | graph ingest | L15/graph inertness |

## Severity summary

**No unresolved architectural HIGH or CRITICAL remains.** Every entry above is a
defect in the CURRENT implementation, not a contradiction in the target
architecture:

- **CRITICAL: none.**
- **HIGH: none.** DEBT-11 is marked *HIGH as user-facing* because the live Q&A
  surface answers legal questions from an ingestion flag today; as an
  architectural matter it is closed by the retrieval and frontend contracts.
- **MEDIUM: 16** — DEBT-01 through DEBT-16.
- **LOW: 1** — DEBT-17.

Architectural acceptance was reached only after an adversarial review in which
every finding was either closed by an explicit decision or reclassified against
verified source. Where a severity was lowered, the reasoning is recorded in the
decision supersession index rather than left implicit.

## On DEBT-17 specifically

This entry carried a HIGH label for much of the programme. Verification retired
it: the fields have **no read path into contract retrieval** — the query has no
ordering clause, the candidate builder discards both fields, and the graph score
derives from relation topology alone. No other backend consumer reads them.

It is **misleading persisted state and a future footgun** — a trap for whoever
next decides to order by priority — **not a live authority leak**. The
acceptance gate pins the inertness so that a future ordering change cannot
silently make the graph authoritative.

## Outstanding non-defect item

**Frontend prototype: OUTSTANDING FOLLOW-ON.** The frontend-surfaces ticket is a
prototype-type ticket and asked for a rough HITL artefact to react to. It was
deliberately deferred because the controlling session prohibited frontend code.

It is **not an unresolved architecture decision** — the semantics are frozen in
[06](contract_master_v1_06_frontend_semantics.md), which is its specification. It should happen
before frontend implementation tickets are finalised.
