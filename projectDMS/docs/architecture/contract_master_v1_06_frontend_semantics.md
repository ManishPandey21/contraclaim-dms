# Contract Master v1 — frontend semantics

Current state. Semantics and interaction boundaries only — not visual design,
not component structure. See [00](contract_master_v1_00_overview.md).

## The purpose of this document

To ensure the UI cannot misrepresent ownership, scope, classification,
applicability, projection status, publication authority, evidence readiness or
reconciliation state — by collapsing them into a single **Active / Ready /
Searchable / Approved** flag.

## Seven orthogonal dimensions, not one status

| # | Dimension | Values |
|---|---|---|
| 1 | **Lifecycle** | `GENERIC DOCUMENT` · `RECONCILIATION CANDIDATE` · `AUTHORITATIVE` · `ARCHIVED` |
| 2 | **Scope** | `ORGANIZATION` · `PROJECT` + anchor — immutable once authoritative |
| 3 | **Classification** | `UNKNOWN` · `SUGGESTED` · `RESOLVED` (+ revision) |
| 4 | **Applicability** | count, per exact `(project_id, contract_id)` |
| 5 | **Projection** | `PENDING` · `IN_PROGRESS` · `CURRENT` · `FAILED` (+ revision match) |
| 6 | **Content authority** | `CONSUMABLE` · `BLOCKED` |
| 7 | **Evidence readiness** | **derived** |

These are genuinely independent. An instrument can be `RESOLVED` + `PENDING`,
`PROJECT` scope with zero applicability pre-promotion, or `APPLICABLE` +
`BLOCKED`. **Any UI with a single status column must misrepresent at least one of
those.**

## Evidence readiness is derived, never stored

```
EVIDENCE READY  ⟺  lifecycle == AUTHORITATIVE
               AND  applicability covers the queried (project_id, contract_id)
                    at the queried mode
               AND  projection_status == CURRENT
                    AND projection_revision == classification_revision
               AND  content authority == CONSUMABLE
               AND  actor is authorised
```

**Never persisted as a flag. Never cached client-side.** Every conjunct can change
without the instrument being touched — an applicability withdrawn elsewhere, a
document blocked, a classification corrected. A cached badge is a stale authority
claim, and it is the one failure a backend cannot defend against.

## The five viewer states

| State | Asserts | Must not imply |
|---|---|---|
| **CATALOGUED / UNASSIGNED** | organisation-owned, zero applicability | not evidence anywhere; not "inactive"; not awaiting an automatic assignment |
| **APPLICABLE — PROJECTION PENDING** | governs this contract; derived clauses not current | **not searchable yet**; not a spinner that always clears — it may go to FAILED |
| **APPLICABLE — EVIDENCE READY** | all gates pass right now | not a permanent property; recomputed per view |
| **APPLICABLE — CONTENT BLOCKED** | applies legally; content withheld | **both facts at once** — not "not applicable", not "deleted" |
| **SUPERSEDED / WITHDRAWN** | applied historically, not currently | not deleted; still answerable under `Historical` |

Each label **names the gate that is open or closed**, never a generic adjective.
Only `EVIDENCE READY` may use success styling. `CATALOGUED` and `SUPERSEDED` are
neutral — correct final states, not problems. `CONTENT BLOCKED` is a warning,
never an error: nothing has gone wrong.

**"Active" is banned from all five.** It is the adjective that collapses seven
dimensions into one.

## Surfaces

**Three new, one extension.**

| Surface | Verdict | Why |
|---|---|---|
| organisation catalogue | **new** | org-scoped, contains zero-applicability items, must be invisible to project-tier actors — it cannot share a page project users already reach |
| classification review queue | **new** | operates over reconciliation candidates, not documents; different subject, authority and audience |
| applicability editor | **new** | writes legal lifecycle events; must not live inside a generic edit modal |
| the five viewer states | **extend the existing viewer** | its status badge is exactly where the misrepresentation lives today |

## Upload interaction

```
Scope:  ( ) Organisation    ( ) Project        ← required, no preselection
        └ Project → project selector, required
        └ Organisation → project selector hidden AND not submitted
```

- the discriminator is **submitted affirmatively**; the form cannot submit with
  neither chosen;
- an empty project selector is **not a scope signal** — it blocks submission
  under `Project` and does not exist under `Organisation`;
- the navbar project **may pre-fill the selector**; it may not preselect
  `scope_level`. **Clearing the project invalidates the form rather than
  switching scope;**
- client-persisted state may restore a *selector value*, never a discriminator.

## Type suggestion vs confirmation

`SUGGESTED` and `RESOLVED` must be visually and semantically distinct.
Confirmation requires an **affirmative act** — never a preselected control that
becomes authoritative because the user pressed Submit for another reason.

Filename-derived suggestions are labelled as filename-derived, so the user knows
what they are confirming. `other_contractual_document` is a deliberate choice,
never the default for unknown.

## Views

- **Catalogue** (`CONTRACT_CATALOGUE_BROWSE`): organisation-owned instruments
  including zero-applicability ones, shown as *catalogued / unassigned*, never as
  active evidence.
- **Project view**: only instruments reachable through explicit applicability.
  **Never the organisation catalogue because the organisation matches.**
- **Contract view**: the exact `(project_id, contract_id)` pair. The same
  `contract_id` in two projects are **different contracts** and must never merge
  in a list, route, URL or cache key.

## Actions

**Apply · Withdraw · Supersede** are separate, explicitly labelled, individually
confirmed actions requiring `CONTRACT_APPLICABILITY_MANAGE`. They are **never**
reachable from a generic edit-metadata modal and never from a bulk row action.

**Legal effects must never be presented as priority, ranking, ordering or
document-type precedence.** That framing is the conflation this architecture
forbids, and presenting it that way would teach users the wrong model. No
legal-effect editor is designed in v1.

**Classification correction is not a text-field edit.** It starts a new revision,
moves projection to `PENDING` and disables evidence use. The UI discloses that
consequence **before** the change. An edit that appears to take effect
immediately in search results is a lie about a legal fact.

**Projection state** is surfaced wherever readiness is claimed or denied.
`FAILED` offers an operator retry and **never falls back to the previous
generation**.

**Supporting-document attach** appears only for project-scoped instruments — the
backend forbids it for organisation-scoped by design, so the UI must not offer a
control that fails.

**Authoritative scope has no generic editable control.** **Archive is not
delete** — applicability, classification and provenance history survive, and the
instrument stays answerable historically.

## Q&A

Requires **project + contract + query mode**. `Historical` requires an
`event_date`, with no default-to-today and no silent fallback.

Cannot enter Q&A: a browse result, a catalogue item with no applicability to the
chosen contract, or anything not evidence-ready.

**Ingestion status is never an evidence gate.** The only legitimate breadth
control is "all instruments applicable to this contract at this mode" — a
canonical set resolved from applicability, not a filter over upload state.

**Zero applicable evidence is an explicit empty state** — with **no widening
offered**. Offering "search all documents instead" as a recovery would undo the
containment architecture at the UI layer.

## Degraded results and provenance

A degraded answer must be **visibly distinguishable from a complete one**, with
the failed sources reachable. Minimum contract: `Complete` / `Degraded`.

Provenance-unrecorded interactive answers have **Save, Export and
Cite-as-record disabled** — not hidden then failing. If provenance persistence
fails, they stay disabled and the failure is visible.

## Migration review queue

Per candidate, show **why promotion is blocked**, on each axis independently:
scope ambiguity (naming the conflicting signals) · type ambiguity · type unknown
· missing canonical document · conflicting project or contract signals · invalid
identity · quarantined document · revalidation required.

**A bare "Needs Review" chip is forbidden** — scope and type resolve
independently, so a candidate is routinely half-resolved and the operator needs
to know which half.

**No batch auto-promote, no "migrate all".**

## Promotion

Promotion is **not "make searchable"**. It creates authoritative legal facts;
projection and evidence readiness follow and may fail.

- **Project-scope promotion** requires the initial applicability decision in the
  same action. The upload's project anchor is **never presented as, or
  substituted for, `APPLIED`**.
- **Organisation-scope promotion** completing with zero applicability is **final
  and correct**. The UI must not require or suggest an assignment to "finish" it.

After promotion the instrument shows `AUTHORITATIVE` + `PROJECTION PENDING`, not
"done".

## Errors

| Class | Meaning | Recovery |
|---|---|---|
| invalid scope input | discriminator missing or contradictory | fix the form |
| permission denial | actor lacks the capability | none; name the capability |
| state / revision / frozen conflict | someone else acted, or the target is frozen | reload and re-decide |
| projection failure | derived state stale | operator retry |
| no applicable evidence | **valid empty, not an error** | none — do not offer widening |
| degraded retrieval | partial sources | show which |

Rendering "no applicable evidence" as an error invites the user to look for a way
around it.

## Authority boundary

The frontend hides or disables for usability. **The server remains the authority
boundary**, and visibility decisions come from a **server-provided capability
response, never a client-side role-name test** — a role test in the browser
recreates exactly the defect the upload path already suffers from.

## State / action matrix

| State | Actions | Evidence / Q&A |
|---|---|---|
| generic uploaded Document | promote to candidate | no |
| candidate: scope resolved, type unresolved | adjudicate type | no |
| candidate: type resolved, scope ambiguous | adjudicate scope | no |
| promotion-ready | promote (+ applicability if project) | no |
| org instrument, unassigned | apply to a contract; correct classification | **no** |
| project instrument, applicable | withdraw; supersede; correct classification | only if projection and content allow |
| projection pending | wait; view metadata | **no** |
| projection failed | operator retry | **no** |
| evidence ready | full use | **yes** |
| applicable, content blocked | view applicability basis | metadata only |
| archived | restore per workflow | historical only |
| degraded Q&A result | retry; view detail | shown, never as complete |
