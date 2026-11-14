# Contract Master v1 — acceptance and release gates

Current state. **No test in this document exists yet.** Nothing here is marked
passing, and no future invariant is treated as satisfied because a design
document asserts it.

**ARCHITECTURE ACCEPTED is not IMPLEMENTATION VERIFIED.**

## Status vocabulary

| Status | Meaning |
|---|---|
| `EXISTING CHARACTERIZATION` | a test exists today and pins current behaviour |
| `DESIGNED` | specified here; not written |
| `NOT IMPLEMENTED` | needs a Contract Master feature that does not exist |
| `BLOCKED` | needs another gate first |

## What already exists

A 24-test characterization suite pins the invariants that hold today: contract
identity, the project-less relationship refusals, and that publication authority
precedes fusion, count and pagination across all three candidate paths. It
records the candidate-capacity gap as a known limitation, and five mutation
proofs were confirmed red.

One recorded coverage gap: the post-hydration authority pass is pinned
structurally only, because reproducing the identity-coercion drift needs real
database identity semantics.

## The canonical release-gating matrix

| ID | Invariant | Layer | Level | Real DB | Mutation proof | Exists | Depends on | Gating | Status |
|---|---|---|---|---|---|---|---|---|---|
| **A-01** | contract identity `(project_id, contract_id)` preserved end to end | domain | integration | — | — | **yes** | — | yes | EXISTING CHARACTERIZATION |
| **A-02** | publication authority precedes fusion, count, pagination | retrieval | integration | — | yes | **yes** | — | yes | EXISTING CHARACTERIZATION |
| **A-03** | project-less relationship targets refused | relationships | integration | — | — | **yes** | — | yes | EXISTING CHARACTERIZATION |
| **L15-01** | eligible predicate precedes each source's limit; an applicable candidate is not starved | retrieval | integration | **yes** | — | no | eligible set | **yes** | NOT IMPLEMENTED |
| **L15-02** | an unresolvable canonical document is excluded by the positive set, not merely un-blocked | retrieval | integration | — | **yes** | no | eligible set | **yes** | NOT IMPLEMENTED |
| **L15-03** | C1/C2 within a project, and the same `contract_id` across projects, stay isolated | retrieval | integration | **yes** | **yes** | no | applicability | **yes** | NOT IMPLEMENTED |
| **L15-04** | a stale projection cannot consume candidate capacity | retrieval | integration | — | **yes** | no | projection state | yes | NOT IMPLEMENTED |
| **L15-05** | an empty eligible set issues no query and does not widen | retrieval | unit | — | **yes** | no | eligible set | yes | NOT IMPLEMENTED |
| **L15-06** | resolver failure hard-fails; no generic fallback search | retrieval | integration | — | — | no | resolver | **yes** | NOT IMPLEMENTED |
| **A-06** | a browse result cannot enter an evidence consumer | consumer | unit (type) | — | **yes** | no | result types | **yes** | NOT IMPLEMENTED |
| **A-16** | no ordering by graph priority for contract evidence | retrieval | structural guard | — | **yes** | no | — | **yes** | DESIGNED |
| **A-21** | `Historical` requires a date; no mode fallback | consumer | unit | — | — | no | query modes | **yes** | NOT IMPLEMENTED |
| **A-24** | canonical override beats higher relevance | consumer | integration | — | — | no | legal effect | **yes** | NOT IMPLEMENTED |
| **A-25** | unresolved precedence surfaces as unresolved; neither asserted | consumer | integration | — | — | no | legal effect | **yes** | NOT IMPLEMENTED |
| **A-31/32** | a source failure cannot yield a complete-looking legal answer | consumer | integration | — | **yes** | no | degraded contract | **yes** | NOT IMPLEMENTED |
| **A-37** | provenance-write failure prevents the accepted state | consumer | integration | **yes** | — | no | provenance | **yes** | NOT IMPLEMENTED |
| **A-39** | contract evidence is gated by the positive set, so the two authority helpers cannot diverge in effect | retrieval | integration | — | **yes** | no | eligible set | **yes** | NOT IMPLEMENTED |
| **C08-02** | a correction makes the prior projection unusable for evidence while rows stay readable | classification | integration | — | **yes** | no | projection state | **yes** | NOT IMPLEMENTED |
| **C08-03** | a late worker cannot overwrite a newer projection | classification | integration | **yes** | — | no | fencing | **yes** | NOT IMPLEMENTED |
| **C08-04** | reprojection failure never rolls back classification | classification | integration | — | — | no | projection state | **yes** | NOT IMPLEMENTED |
| **C08-05** | stale vectors cannot gain authority | retrieval | integration | — | **yes** | no | eligible set | yes | NOT IMPLEMENTED |
| **C08-06** | stale graph type/priority cannot gain authority | retrieval | integration | — | **yes** | no | — | yes | NOT IMPLEMENTED |
| **C08-07** | clause rows cannot self-authorise | clause | unit | — | — | no | — | yes | NOT IMPLEMENTED |
| **C08-08** | a filename rename changes no authoritative type | classification | unit | — | — | no | classification facts | yes | NOT IMPLEMENTED |
| **C08-09** | a classification correction creates no applicability or legal effect | classification | integration | — | **yes** | no | — | yes | NOT IMPLEMENTED |
| **C08-10** | a stale projection cannot be presented as complete evidence; it hard-fails | consumer | integration | — | — | no | consumer contract | **yes** | NOT IMPLEMENTED |
| **C08-11** | contract retrieval withholds non-current and non-authorised clauses, matching the sibling stack | retrieval | integration | **yes** | — | no | store alignment | **yes** | NOT IMPLEMENTED |
| **C08-12** | clause identity contains no classification component | clause | unit | — | — | no | — | yes | DESIGNED |
| **M09-01** | an empty legacy project never promotes organisation scope | migration | integration | — | **yes** | no | classifier | **yes** | NOT IMPLEMENTED |
| **M09-02** | a retained audit null yields ambiguity plus a hint, never confirmed organisation scope | migration | integration | — | **yes** | no | classifier | **yes** | NOT IMPLEMENTED |
| **M09-02b** | an absent audit row yields no inference in either direction | migration | integration | — | **yes** | no | classifier | yes | NOT IMPLEMENTED |
| **M09-03** | scope and type resolve independently; a resolved axis is never spent as partial authority | migration | unit | — | — | no | classifier | yes | NOT IMPLEMENTED |
| **M09-04/05** | suggested type cannot promote; unknown never maps to *other* | migration | unit | — | — | no | classifier | yes | NOT IMPLEMENTED |
| **M09-06** | ambiguous scope performs zero authoritative writes | migration | integration | **yes** | — | no | promotion | **yes** | NOT IMPLEMENTED |
| **M09-07** | project-scope promotion atomically creates at least one applicability | migration | integration | **yes** | — | no | promotion | **yes** | NOT IMPLEMENTED |
| **M09-08** | organisation-scope promotion succeeds with zero applicability | migration | integration | — | — | no | promotion | yes | NOT IMPLEMENTED |
| **M09-09** | an unknown effective-from stays null | migration | unit | — | — | no | applicability | yes | NOT IMPLEMENTED |
| **M09-10** | two concurrent identical promotions converge on one record and one applied event | migration | integration | **yes** | — | no | claim/lease | **yes** | NOT IMPLEMENTED |
| **M09-11** | conflicting operator classifications fail closed; the loser is told, never merged | migration | integration | **yes** | — | no | claim/lease | **yes** | NOT IMPLEMENTED |
| **M09-12** | a stale reconciliation snapshot is revalidated before promotion | migration | integration | — | — | no | fingerprint | yes | NOT IMPLEMENTED |
| **M09-13** | lease expiry changes no legal state | migration | integration | **yes** | **yes** | no | claim/lease | yes | NOT IMPLEMENTED |
| **M09-14** | a blocked document cannot become readable through promotion | migration | integration | — | — | no | promotion | **yes** | NOT IMPLEMENTED |
| **M09-15** | promotion does not imply projection current | migration | integration | — | — | no | projection state | **yes** | NOT IMPLEMENTED |
| **M09-16** | an old projection cannot become evidence after promotion | migration | integration | — | **yes** | no | projection state | yes | NOT IMPLEMENTED |
| **M09-17** | legacy source fields remain untouched | migration | integration | — | — | no | — | yes | NOT IMPLEMENTED |
| **M09-18** | a derived-store outage does not roll back valid promotion, and evidence stays disabled | migration | integration | — | **yes** | no | promotion | yes | NOT IMPLEMENTED |
| **M09-19** | the promotion receipt links candidate to record deterministically | migration | integration | **yes** | — | no | promotion | yes | NOT IMPLEMENTED |
| **M09-20** | a cross-organisation candidate fails closed | migration | integration | — | — | no | classifier | yes | NOT IMPLEMENTED |
| **M09-21** | dry run writes nothing at all | migration | integration | — | **yes** | no | control plane | **yes** | NOT IMPLEMENTED |
| **M09-22** | session evidence captured at inventory survives TTL expiry | migration | integration | **yes** | — | no | reconciliation row | yes | NOT IMPLEMENTED |
| **U12-01** | explicit organisation scope persists a positive discriminator through to the authoritative record | upload | integration | — | — | no | scope capture | **yes** | NOT IMPLEMENTED |
| **U12-02** | project scope requires a server-validated anchor | upload | integration | — | — | no | scope capture | yes | NOT IMPLEMENTED |
| **U12-03** | missing or blank project never implies organisation — the request is rejected | upload | unit | — | **yes** | no | scope capture | **yes** | NOT IMPLEMENTED |
| **U12-04** | scope intent survives session TTL expiry | upload | integration | **yes** | — | no | reconciliation row | yes | NOT IMPLEMENTED |
| **U12-05** | a project-tier actor cannot create an organisation-scope instrument | upload | integration | **yes** | **yes** | no | permissions | **yes** | NOT IMPLEMENTED |
| **U12-06** | a selected contract creates no applied event | upload | integration | — | **yes** | no | scope capture | **yes** | NOT IMPLEMENTED |
| **U12-07** | scope cannot silently change on idempotent retry | upload | integration | **yes** | — | no | session state | yes | NOT IMPLEMENTED |
| **U12-08** | authoritative scope cannot change through a generic metadata edit | upload | integration | — | — | no | immutability | yes | NOT IMPLEMENTED |
| **U12-09** | a filename heuristic cannot produce a resolved type | upload | unit | — | — | no | classification | yes | NOT IMPLEMENTED |
| **U12-10** | uploaded + classified + projection-pending is not evidence-capable | upload | integration | — | — | no | projection state | **yes** | NOT IMPLEMENTED |
| **U12-11** | a multipart batch preserves explicit scope for every file | upload | integration | — | — | no | scope capture | yes | NOT IMPLEMENTED |
| **U12-12** | a legacy omitted project goes to rejection or reconciliation, never organisation scope | upload | integration | — | **yes** | no | compatibility | **yes** | NOT IMPLEMENTED |
| **P-01** | both new permissions remain entitlement-scoped | RBAC | integration | **yes** | — | no | permission list | **yes** | NOT IMPLEMENTED |
| **P-02** | project admin holds neither new permission after seeding | RBAC | integration | **yes** | **yes** | no | exclusion set | **yes** | NOT IMPLEMENTED |
| **F13-01** | the scope choice is explicit and non-nullable | frontend | component | — | — | no | scope capture | **yes** | NOT IMPLEMENTED |
| **F13-02** | a cleared project cannot become organisation scope | frontend | component | — | **yes** | no | scope capture | **yes** | NOT IMPLEMENTED |
| **F13-03** | a project admin cannot reach organisation-scope creation | frontend | browser | — | — | no | permissions | yes | NOT IMPLEMENTED |
| **F13-04** | suggested and resolved classification differ visibly | frontend | component | — | — | no | classification | yes | NOT IMPLEMENTED |
| **F13-05/06** | projection pending and failed both disable evidence use | frontend | integration | — | — | no | projection state | **yes** | NOT IMPLEMENTED |
| **F13-07** | promotion does not imply evidence-ready | frontend | integration | — | **yes** | no | derived readiness | **yes** | NOT IMPLEMENTED |
| **F13-08** | an unassigned organisation item displays as catalogued, not active evidence | frontend | integration | — | — | no | catalogue | yes | NOT IMPLEMENTED |
| **F13-09** | a browse result cannot launch Q&A | frontend | browser | — | — | no | result types | **yes** | NOT IMPLEMENTED |
| **F13-10** | historical mode requires a date | frontend | browser | — | — | no | query modes | **yes** | NOT IMPLEMENTED |
| **F13-11** | a degraded result cannot appear complete | frontend | integration | — | — | no | degraded contract | yes | NOT IMPLEMENTED |
| **F13-12** | unrecorded provenance disables persist and export | frontend | integration | — | — | no | provenance | yes | NOT IMPLEMENTED |
| **F13-13** | post-promotion scope has no generic edit control | frontend | component | — | — | no | immutability | yes | NOT IMPLEMENTED |
| **F13-14** | a selected contract creates no implicit applicability | frontend | integration | — | **yes** | no | scope capture | yes | NOT IMPLEMENTED |
| **F13-15** | content-blocked and applicable render as two dimensions | frontend | component | — | — | no | viewer states | yes | NOT IMPLEMENTED |
| **F13-16** | the same `contract_id` across projects stays isolated in view and route | frontend | integration | — | — | no | contract identity | yes | NOT IMPLEMENTED |
| **F13-17** | evidence readiness derives from current backend state and cannot be a stale client flag | frontend | browser | — | **yes** | no | derived readiness | **yes** | NOT IMPLEMENTED |

## The real-database contract

These properties are **database semantics**. A fake collection satisfies them by
construction and proves nothing:

- **transactional promotion** — all-or-nothing across several documents;
- **concurrent promotion and late-worker fencing** — a conditional write is a
  database primitive; an in-memory double serialises for free;
- **applicability lifecycle and replay ordering** — real ordering and real
  transaction boundaries;
- **TTL survival** — a fake never expires anything;
- **candidate-limit starvation** — the whole point is the interaction of sort,
  limit and real index behaviour; a fake that ignores limits passes vacuously;
- **same-contract isolation** — needs real query semantics, not a dictionary
  scan;
- **permission seeding and the bulk-grant exclusion** — needs the real seed path;
- **scope retry** — needs persisted session state;
- **provenance acceptance lifecycle** — needs a real failed write.

The programme has already been burned by fake-collection tests: a shared test
double once matched unknown operators silently, which made lease queries
vacuously true, and separately allowed duplicate ids.

## The mutation-proof contract

Twenty-two gates above require mutation proof, and they share one shape: **the
assertion is that something does *not* happen.** Such a test passes by
construction unless the alternative is actually exercised.

Two examples the programme has already hit:

- a graph-inertness assertion passes for free because the field is already
  inert — so the mutation must introduce the ordering the test forbids;
- a client-cached readiness flag passes every static test — only a mid-session
  authority change turns it red.

Two of the existing characterization tests were themselves found defective by
mutation: one stayed green while contract identity was dropped, and one was
vacuous by construction. Both were replaced, and one was removed rather than
fabricated, with the coverage gap recorded instead.
