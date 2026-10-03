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

---

## Reprojection runtime (2026-09-26, `fix/contract-master-reprojection-runtime`)

**Closed: promotion no longer leaves an instrument PENDING forever.** The 2026-09-25
staging rehearsal proved `ContractReprojectionWorker` and `PostPromotionSequencer`
had no runtime caller: every promoted instrument stayed `projection_status=PENDING`
and Contract Master evidence answered `200 valid_empty` for a contract whose clause
text was indexed.

What exists now:

- **One runtime owner** — `services/contract_reprojection_runtime.py`, started only by
  `rbac_backend.worker` when `START_CONTRACT_REPROJECTION_WORKERS=true` (set on the
  `contract-worker` service only; default `false`; `main.py` never starts it).
- **The instrument row is the durable job.** Promotion and classification correction
  already commit `projection_status=PENDING` with the authoritative write, so there is
  no post-commit enqueue that can fail or be lost. The runtime polls
  `projection_status != CURRENT OR projection_revision != classification_revision`
  every `CONTRACT_REPROJECTION_POLL_SECONDS` (default 15). The Redis contract-ingest
  queue was deliberately not reused: its job identity and status hash are the
  upload's.
- **Job identity** `contract-reprojection:{contract_document_id}:{classification_revision}`
  in `contract_reprojection_claims`: retries of N converge, N+1 is a separate key.
- **Lease hardening**: an expired lease (or a failure past its backoff) is taken over by
  one conditional upsert; `DuplicateKeyError` is the only "held" answer, a datastore
  error propagates. Every terminal write checks the owner token. Failures back off
  1, 2, 4… minutes; after `REPROJECTION_MAX_ATTEMPTS` (6) the generation stays FAILED
  (`exhausted`) until the revision moves or an operator clears the claim.
- **The build is real** (`services/contract_projection_builder.py`): canonical Document
  publication check → persisted page text (`contract_ocr_pages.cleaned_text`) → the
  upload clause pipeline (`ContractIngestor.extract_clause_set` /
  `_build_clause_payloads`) → `EmbeddingClient.embed(strict=True)` (no deterministic
  fallback) → vector upsert with the upload's point ids → the upload's graph write
  (best effort, outcome logged) → `document_vectors` rows.
- **Atomic publication**: the rows replace the document's contract rows inside the
  same transaction as the owner-token check and the `classification_revision == N`
  CURRENT stamp. A superseded or lost generation commits nothing.
- **Evidence no longer reads a PENDING/FAILED applicable instrument as "nothing
  applies".** The resolver reports it (`projection_not_current`) and the evidence route
  answers `409 projection_not_current: …` (C08-10's hard-fail class). `valid_empty`
  now means no applicable instrument whose document may publish (an instrument whose
  document is held for review or quarantined is excluded, not named - see residuals).

Review round (4 independent reviews) closed before commit:

- a revision-N worker could still write vectors after N+1 published → every vector batch
  (and the graph write) re-proves owner **and** live revision first (`worker.verify`);
- a ~31-minute outage exhausted every generation → clean failures retry forever, backoff
  capped at 60 minutes; only crash loops (6 lease expiries with no recorded outcome)
  exhaust, and those are swept to a visible FAILED;
- a redeploy stranded the in-progress build for a whole lease → cancellation releases the
  claim at once without spending the crash budget;
- evidence filtered rows by the ingest `project_id`, so an organisation-scoped
  instrument's CURRENT projection was unreadable → evidence narrows by organisation and
  the eligible set only (v1_02: row project is provenance, never authority);
- text/DOCX contracts have no page store → projected from the stored file with the same
  parser and cleaner as ingest;
- an OCR retry or reindex rewrote rows under a CURRENT generation → a completed ingest of
  a promoted document moves its instrument back to PENDING and re-opens the generation; a
  build in flight at that moment is revoked (owner token rotated) and cannot publish;
- superseded vector points of the document are deleted on rebuild, and the deletion is
  proven by re-listing (a surviving orphan fails the build); lexical rows no longer
  carry the embedding (keeps the publication transaction small);
- operators see `projection_work` (status, failures, last_error, next_attempt_at,
  exhausted) on `GET …/projection`; `ContractReprojectionWorker.request_retry` re-opens a
  generation (CLI: `rbac_backend.scripts.contract_reprojection_retry`).

Review round 2 (4 independent reviews, 2026-09-26) closed before commit:

- **HIGH** — `request_retry` made three separate writes; a completion transaction
  committing between them left the instrument PENDING beside a `complete` claim, which
  every later `claim()` answered as contention: PENDING forever by a new route. Now the
  claim re-open/revoke (one write, any state) and the instrument PENDING commit in one
  transaction that serialises against `complete()`; and `claim()` takes over a `complete`
  claim whenever the instrument still reports the generation as due (the row is the queue).
- a failed or half-written re-ingest left CURRENT standing over rewritten rows → the
  ingest withdraws the projection *before* touching rows (failure aborts the ingest),
  again on failure, and again on completion; the builder refuses a source whose ingest is
  in flight (visible FAILED, re-opened when the ingest settles);
- the storage vector repair rewrote a governed document's points (LangChain shape,
  non-strict embeddings, no revision) → it delegates to reprojection (`status=delegated`);
- orphan removal deleted by payload chunk id, which is not the point id of
  LangChain-written points → it deletes by point id (`VectorClient.list_points`);
- a build could certify vectors written to a collection evidence never searches (a
  configured name that differs, or the dimension-mismatch `_dim<N>` fallback) → the build
  fails unless the store wrote to `document_vectors`, the namespace evidence reads;
- the live `VectorClient` kept every upserted vector in its in-memory stand-in index for
  the life of the contract-worker → only a disabled store keeps one;
- heartbeat (owner + live revision) also precedes the graph write; the projection route
  no longer returns `worker_id` and redacts endpoints from `last_error`.

Review round 3 (a parallel session, 2026-09-26; its reviews were not reproduced - each item
below was re-audited hunk by hunk and is pinned by a test that fails when the hunk is
reverted, see the provenance audit):

- the projection wrote vectors to the store's configured default collection and then
  failed unless that was `document_vectors`; with `QDRANT_COLLECTION=contracts` (the
  value in `.env.example`) every generation would have FAILED forever → upsert, list and
  delete name the evidence namespace explicitly (`namespace="document_vectors"`); only
  the `_dim<N>` fallback can still land elsewhere, and that still fails the build;
- `claim()` read the instrument before its upsert, so a completion committing in between
  let a second worker take over the `complete` claim and rebuild under CURRENT → the claim
  re-reads the instrument after the upsert and hands a CURRENT generation straight back;
  `fail()` never stamps FAILED over CURRENT; the crash sweep re-checks lease expiry and
  attempts in its own write; a claim cancelled before the caller holds it is given back;
- the storage vector repair re-opened even a CURRENT generation, and bulk resync never
  recorded it, so every bulk run moved an organisation's evidence to 409 → it re-opens only
  a FAILED generation, leaves CURRENT/PENDING alone, records `sync_status=delegated`, and
  `/reconcile` reports `delegated`, not `repaired`;
- `POST /admin/vector/reconcile` treated every contract point as "missing in Mongo"
  (contracts have no `chunks` rows) and deleted a CURRENT projection's vectors → it skips
  contract documents and governed documents (`skipped_contract`);
- the general reprocess pipeline replaced a governed contract's clause rows with token
  chunks while the instrument stayed CURRENT → `process_document` answers 409 and
  `process_document_async` refuses a document any instrument names;
- orphan removal kept a foreign-shaped point whose payload named a kept chunk → a point is
  kept only by its point id; `last_error` on the projection route no longer carries
  `host:port` or filesystem paths.

Review round 3 (final-tree reviews, two HIGH) closed, each with a test that fails with the
fix reverted:

- `POST /api/v1/ingestion/jobs` pruned every `document_vectors` point of a governed contract
  it had not just written while the instrument stayed CURRENT (the `valid_empty` class) →
  the route answers 409 after authorisation and `IngestionPipeline.process_job` refuses a
  governed document at entry and again at the write boundary, for both id spellings;
- two ingests of one document could write its source at once, and a projection could be
  built from a half-written source whose `status` another ingest had overwritten → a
  durable per-canonical-document source lease (`contract_source_leases`): one writer, lease
  renewed while running, reclaimable when it lapses, a lapsed owner cannot finalize, and
  its late writes taint the source (failed until a clean ingest). The builder requires a
  settled source at start and the same settled generation inside the completion
  transaction. The queue re-queues a busy ingest without spending a retry and drops a
  redelivered copy of a live run;
- a legacy string-keyed Document whose id looks like an ObjectId never showed `processing`
  → `update_contract_document` / `get_contract_document` resolve through
  `resolve_canonical_document`;
- a stale build's orphan cleanup could delete a newer generation's points → ownership,
  revision and source generation are re-proved after listing and before deleting;
- a CURRENT projection with its points gone stayed evidence-ready → storage repair
  verifies CURRENT against the stored rows and hands a damaged generation back (PENDING);
- bulk resync re-selected governed contracts every run (limit spent, backoff reset) → they
  are excluded in the query;
- cheap fences: `request_retry` reads the live revision inside its transaction and
  supports `only_if_failed` (storage repair), the crash sweep moves claim and instrument in
  one transaction, a governed document's general extraction job is closed terminally with
  nothing written;
- operations: image revision label + `RELEASE_SHA`, a runtime heartbeat, and the
  `post_deploy_verify.sh` ownership section (OPERATIONS 7a).

Review round 4 (the fixes above, re-reviewed; five more HIGH) closed, each with a test that fails
with the fix reverted:

- `POST /api/v1/ingestion/jobs` authorised only the caller-supplied scope, never the document
  (pre-existing): another tenant's text was chunked under the caller's org, and the governance
  409 disclosed which foreign documents are contracts → the document must live in the payload
  scope and pass `authorize_document`; absent, foreign and forbidden all answer 404; the pipeline
  refuses a job whose scope is not the document's;
- a transient error while recording a failed ingest let the fallback restore the pre-ingest
  `settled` over half-written text → a touched source is only ever failed (or left to lapse);
- an ingest that could not renew kept writing → it cancels itself before its lease can lapse;
  a settle degraded by a taint is a failed ingest (`SourceTainted`), not a completed one; the
  taint is one write;
- a refused general job left the document `queued`/`retrying` and blocked its projection
  forever → the builder treats only a write under way (`processing`) and the source lease as
  in flight;
- `scripts/reconcile_vectors.py --repair` rewrote governed contracts' evidence points →
  delegated;
- storage repair with a disabled vector store read every point as missing and withdrew every
  CURRENT projection → unverified, no action; points compared by point id, extra points
  detected, the re-open fenced on the verified revision (`only_if_current_at`);
  `/storage-sync/reconcile` excludes governed contracts like bulk resync;
- the heartbeat was written only after a pass and old containers' rows read as second owners →
  liveness during passes, row removed on stop, TTL, strangers only among running
  backend/document-worker containers;
- queue: a lost-lease run closes its hash (`superseded`) so later enqueues are not swallowed, a
  self-held copy keeps its processing entry for recovery, a waiting job is re-queued even when
  its worker stops, the job heartbeat survives a Redis error;
- the generic pipeline re-checks governance immediately before its prune.

Review round 5 (re-review of round 4; three HIGH, all in the self-fence) closed, each with a test
that fails with the fix reverted:

- a lease loss noticed by the renewal task left the source untainted → it taints, so the new
  owner's settle reports a failed ingest;
- a renewal tick after `settle` read the released lease as lost and cancelled a finished ingest
  (document left `processing`, job discarded) → the renewal is stopped before every settle, fail
  or release;
- a self-fence with no known new owner was reported as a lost lease and discarded →
  `SourceLeaseFenced`, recorded as a failed ingest (job, document) and retried; the consumed
  cancellation is `uncancel`led so the queue's worker task is not left cancelling (3.11+);
- `settle` decided a taint by reading the row back after releasing it → decided by the write;
- a job waiting out a backoff or its turn was off every list while it slept → it stays on the
  processing list (hash `queued`) until it is back on the queue;
- storage repair reads rows and points twice before withdrawing a generation; the runtime writes
  liveness for the whole pass (a ticker), and its heartbeat index can no longer stop
  reprojection; the ingestion route maps a document-level 403 to the same 404; the reconcile
  script records delegation only when repairing and never over `mismatch`;
  `post_deploy_verify.sh` fails when no backend container can run the liveness check.

Review round 6 (re-review of round 5; one HIGH) closed, each with a test that fails with the fix
reverted:

- an early failure (before the owned handler) released the lease while the renewal ran; its next
  tick cancelled the caller - the queue's only worker loop → the ingest runs in its own task and
  the renewal can only ever stop that task; every release path stops the renewal first;
- a lease lost with no live owner left (the taker settled or lapsed) was dropped as superseded →
  a recorded, retried failure (the tainted source needs a clean ingest);
- a failed taint write was not retried → retried; the publication transaction now *writes* the
  lease row (conditional on settled at the built generation), so an acquire or taint committing
  during publication is a write conflict instead of a snapshot read slipping past it;
- stale-job recovery re-queued jobs waiting out a backoff or their turn → they are `waiting`,
  recovered only when their worker's heartbeat is stale;
- the liveness ticker kept a hung pass looking healthy → the pass start is recorded and the
  post-deploy check fails a pass running past the 30-minute claim lease;
- the reconcile script's conditional upsert inserted a second status row beside `mismatch` →
  one row per document.

Review round 7 (re-review of round 6; no BLOCKER, no HIGH) closed, each with a test that fails
with the fix reverted:

- round 6 retried a lease lost with no live owner only when the renewal noticed it; a loss found
  at settle or fail was still dropped as superseded → the queue yields only to a *live* owner, and
  the owned handler records the failure when none is left;
- `enqueue` did not know the new `waiting` status, so a reindex during a backoff delivered the job
  twice → `waiting` is already on its way back; a waiter re-queues only a job still `waiting`;
- a fence after a lease lost to a live holder no longer writes `failed` over that holder's
  statuses; the reconcile script's delegation write is conditional on the row not being
  `mismatch`.

Review round 8 (review of round 7; no BLOCKER, no HIGH) closed, each with a test that fails with
the fix reverted: a lease found lost at `fail()` with no owner now records the failed attempt
(the last attempt no longer leaves the contract `processing`); an unreadable lease holder is
`unknown` and treated as live, never as "no owner"; a waiter whose job was moved on by recovery
no longer removes the new run's processing entry.

Review round 9 (review of round 8; no BLOCKER, no HIGH): an unreadable holder made the queue close
the job as superseded - dropped even with no owner left, or over a live copy of the same job →
it waits and retries without spending an attempt (its next acquire learns the real state);
stale-job recovery retires a leftover `superseded` entry instead of re-running it. Both with a
test that fails with the fix reverted.

Renewal shutdown (2026-09-27): a Python 3.10 hang in the final gates - `quiesce` waited forever
because the renewal loop's `asyncio.wait_for` absorbed the stop's cancellation as the in-flight
renew completed. Fixed without version-specific code: each renew is its own task bounded by
`asyncio.wait`; the stop sets a flag and re-cancels until the renewal has actually ended; the
ingest is awaited with `asyncio.wait`, so a shutdown is told apart from a renewal-requested stop
on every Python version; a renew's outcome is read from the renew itself; the self-fence is
measured from a renew's start and fires three intervals early (worst case ~S+420 s against a
lease expiring at S+600 s, leaving ~180 s for the ingest to stop plus clock skew); a fenced
ingest that finds its lease already taken taints the source. Each with a deterministic test that
fails on the pre-fix code; no renewal happens after quiesce returns (counted against Mongo).

Owner decisions (2026-09-27): the post-deploy image-revision check stays **strict** (a deploy
that does not rebuild the contract-worker fails verification) - **refined by the owner 2026-10-02:** strictness stays,
and becomes service-scoped. A deploy declares `FULL`, `CLIENT_ONLY` or `BACKEND_ONLY`; every
deployed service must run exactly the image its release manifest records, built from the
deployed commit; every other app service must stay on exactly its approved image, with an
unchanged build context; drift and any unprovable identity fail (`scripts/release_manifest.py`,
deployment guide 5a-7a). A client-only deploy rebuilds nothing else and still proves every
backend image unchanged. CURRENT point verification only
in the single-document repair, the silent drop of held/quarantined/deleted instruments from
evidence, and the general pipeline's refusal of governed documents (with the CL-4A tests
promoting after extraction) are **accepted residuals**.

Residual gaps — stated, not waived:

| Gap | Why it does not make evidence lie | Follow-up |
|---|---|---|
| **Owner-accepted residual, MEDIUM, pre-existing (decision 2026-10-02; PR #34 hardening).** The race: a contract ingest that is stopped (self-fence or shutdown) cancels its asyncio task, but Motor 3.7 runs each MongoDB operation on an executor thread and re-sends a retryable write once, and without CSOT no `maxTimeMS` reaches the server - so a page, lexical-row or vector write the stopped ingest already sent can still be applied after its lease is released. A new owner may then be rewriting the same source, and its settle would vouch for text from two writers. Pre-existing: the lease and self-fence shipped with PR #34's base commit 3ca0149 releasing at once; this hardening did not introduce the race, it narrowed it | mitigation in place: the release after a stop holds the source unacquirable for `stop_release_hold()` - two attempts of the client's server-selection + connect + socket timeouts (default 2 min floor; a whole lease if any timeout is 0), including when the stop path's own release failed and the outer handler releases. A retry during the hold waits its turn. Impact if it occurs: a source projected from interleaved text until the next clean ingest; low likelihood (an in-flight write must outlast the hold - a batched write still executing server-side, or a saturated pool wait). Weaker paths: every release attempt failing (the lease then lapses about 3 min after the fence), and an ingest that failed on its own after a client-side `NetworkTimeout`/`AutoReconnect` mid-write (released at once) | follow-up (preferred permanent fix): fence every source write (page store, lexical rows, contract vectors) on the lease generation, and refuse to settle or project a source holding more than one generation. Not CSOT, not in PR #34 (owner decision). |
| `resolve_authorized_project_universe` (letter + arbitration drafting) drops a not-current instrument silently instead of failing | pre-existing behaviour, unchanged; only the evidence route hard-fails today | extend `projection_not_current` to the drafting universe |
| The evidence route's *query* embedding still uses the non-strict client (fake vector when the provider fails) | pre-existing DEBT-15 class; lexical still answers, but the vector source reports success | strict query embedding + `degraded` marking |
| `contract_clauses` rows and Falkor clause nodes carry no `source_classification_revision` | Contract Master evidence reads neither for eligibility (lexical = `document_vectors`, vector = Qdrant payloads, both tagged) | tag when those stores join the evidence path |
| A vector batch already in flight when `verify` passes can land after a newer generation publishes (point ids carry no revision) | the payload tag is not read for admission; rows and CURRENT are transactional | revision-aware point ids |
| `VectorClient.delete` still swallows Qdrant errors (the builder re-lists and fails the build if an orphan survives); its in-memory branch filters by project, not document | builder refuses a disabled store, so that branch is unreachable from projection | fix `VectorClient` |
| Graph sync still writes the filename-derived `section_type`/`priority` (DEBT-03/17 unchanged) | same values as upload; graph is not read for ordering | DEBT-03/17 |
| Operator retry is a CLI (`python -m rbac_backend.scripts.contract_reprojection_retry`), not an audited route | FAILED and its reason are visible on the projection route; clean failures retry automatically | route + audit |
| Clean failures retry hourly forever (each spends embeddings) | visible as FAILED with `failures` count | alert on `failures` threshold |
| One non-current applicable instrument makes the whole contract's evidence answer 409 | fail-closed by design (stale projection is the hard-fail class) | accepted |
| An applicable instrument whose document is held for review, quarantined, soft-deleted or otherwise inactive is dropped silently while it stays CURRENT (the hold/deletion purge removes its rows and points), so evidence can answer `valid_empty` | the publication policy forbids that content as evidence; naming it is a disclosure decision | report blocked instruments separately |
| A revoked build's vector upsert already past its heartbeat can land after the new owner's publish (a stale point beside a CURRENT generation). The orphan sweep is now fenced after listing, so it can no longer remove the new generation's points | one `contract-worker` is deployed; lexical rows and CURRENT are transactional; the point revision tag is not read for admission; storage repair detects missing, not extra, points | revision-aware point ids / post-complete point reconciliation |
| An ingest's page and row writes are not fenced one by one. The ingest fences itself instead: it renews every 60 s and cancels itself when it has not renewed for 7 minutes, 3 minutes before its 10-minute lease can lapse, so no second owner can start while it writes. A worker whose clock runs more than ~3 minutes behind the acquirer's defeats that margin | lease expiry is compared on the acquirer's clock; a stale writer detected at settle taints the source (failed, reported as a failed ingest) | per-write generation fence in `upsert_ocr_pages` / `insert_document_vectors` |
| Generic ingestion: a promotion committing between the write-boundary check and the upsert leaves that job's untagged points in the (caller-chosen) namespace; the prune re-check stops the prune only | the instrument is PENDING then, and the first build's orphan sweep removes them before CURRENT | delete the job's own points on the pre-prune refusal |
| `POST /api/v1/ingestion/jobs` now requires the document's exact org **and** project: organisation-level documents (no project) and documents scoped only by legacy `organizationId` answer 404 | fail-closed | accept, or admit org-scoped documents deliberately |
| The general document pipeline (`process_document_async`) checks governance at entry, not again where it deletes/rewrites rows (`_create_and_store_embeddings`, `_invalidate_stale_publication`); bulk-upload OCR does not check (new documents only) | the window is a promotion committing within milliseconds of the entry check, before `processing` is written | re-check governance at those write sites |
| `services/data_sync.py` (dead `__main__`, pinned by `test_data_sync_has_no_caller`) rewrites vectors without a governance check | no caller; not a route or script entry | delete the module |
| A wiped `document_vectors` *collection* (not just its points) makes verification `unverified`, not `reprojection_required` | reported (`mismatch`), never acted on | recreate the collection (operator step), then repair |
| Storage repair reads rows and points twice before re-opening, fenced on the revision (`only_if_current_at`), not on the exact published generation: a same-revision rebuild publishing between the second read and the retry commit is re-opened once | it rebuilds; evidence answers 409 meanwhile, never stale text | fence on a publication id |
| An ingest ending in `CancelledError` that nobody requested (no source of one was found) is treated as a shutdown and ends that queue worker; after a taint, the re-open of the projection is non-strict, and a taint failing all 3 attempts is lost | both need the clock-skew / stalled-loop trigger of the self-fence residual; a failed invalidation is logged | treat an unrequested cancel as a failed attempt; strict re-open after taint |
| The post-deploy stuck-pass check fails a legitimate pass over 30 minutes, and cannot see a runtime whose every pass raises (the ticker keeps it fresh, each new pass resets the start) | fail-closed for the first; the second shows as FAILED/PENDING instruments on the projection route and `contract reprojection pass failed` in the worker log | record the last pass outcome and alert on consecutive failures |
| An OCR-retry or reindex request arriving while the job is queued, running or `waiting` is dropped by the enqueue dedupe (the route still records it as queued) | pre-existing for queued/running; the job being run re-reads the document | merge the new payload into the pending job |
| Copies of one queue job share one Redis job hash. On a triple fault (a stalled run redelivered, the stale copy losing its lease to the new copy, then the holder read failing), the stale copy writes `waiting`/`queued` over the live copy's `running`, rewinds its attempt count, and can cause one duplicate full re-ingest after the live copy completes | the source lease still admits one writer, so no two runs write the source at once; evidence never lies | owner decision 2026-09-27: accepted; fix is a compare-and-set on the job hash's owning worker, and refusing a popped job whose hash says completed |
| Source-lease timing margins: the fence clock starts when the renewal task first runs (after `acquire`), lease expiry is written with the worker's clock and compared with the acquirer's, and the remaining margin (~180 s) must cover the ingest stopping plus inter-worker clock skew | NTP-synchronised hosts; a stall or skew beyond the margin is caught by settle/fail/renew as a lost lease, which taints the source | server-side `$$NOW` in acquire/renew; start the fence clock before `acquire` |
| After `work.cancel()` the waits on the ingest are unbounded and the fence cancel is sent once; an ingest that swallowed CancelledError would hang or run on without renewals | no such swallowing on the contract ingest path | re-cancel in a loop like `_stop_renewal` |
| `_taint_source` gives up after 3 attempts; the failure is then only logged | needs a sustained datastore outage at that moment | durable pending-taint retry, and an error-level log with the cause |
| A redelivered copy that finds its own job holding the lease does not refund its attempt; after a fenced run whose `fail()` also failed, redeliveries spend retries against the job's own lapsed lease | the lease lapses within 10 minutes; retries are 3 by default | refund the attempt on self-held Busy |
| Repair reports `superseded` when it re-opens a CURRENT instrument that has no `complete` claim (hand-stamped staging rows); `mismatch` is not cleared when the rebuild heals; point listing fetches full payloads | cosmetic / backlog accuracy | follow-ups |
| Point verification of a CURRENT projection runs only in the single-document storage repair; bulk resync and `/storage-sync/reconcile` exclude governed contracts. A partial Qdrant loss leaves those projections CURRENT with the vector source empty (evidence still answers from the lexical source, vector reported `success`) until someone repairs the document | lexical rows and CURRENT are transactional; missing *and* extra points are detected when verified | periodic CURRENT point verification in the reprojection runtime (owner disposition) |
| The post-deploy check requires the contract-worker image to carry the deployed commit, so a deploy that rebuilds only another service (a frontend-only change) fails it | fail-closed: it cannot prove which code the worker runs | owner decision 2026-09-27: kept strict - rebuild the contract-worker on every deploy |
| A governed document is refused by the general extraction pipeline (terminal `skipped_governed_contract`); its re-read is the contract reindex only. The CL-4A cross-regression tests now promote after extraction | the general pipeline would rewrite the evidence rows under a CURRENT projection | owner decision recorded here |
| A datastore outage that also defeats `fail()` leaves the claim to lease expiry, which counts as a crash; six such episodes exhaust the generation | visible FAILED/`exhausted`, operator retry | distinguish datastore-error expiry from crash |
| No aggregate metric/alert for FAILED or long-PENDING instruments | per-instrument state is on `GET …/projection` and in WARNING logs | count FAILED/exhausted + oldest PENDING in `post_deploy_verify.sh` / metrics |
| The retry CLI re-opens a CURRENT generation without a confirmation flag | withdraws evidence until the rebuild, never a legal field | `--force-reopen` for CURRENT + audit event |
| The builder does not check the persisted page rows against the document's page count | pages that failed extraction hold the contract for review upstream (`human_review_required`) | page-count completeness check |
| `complete(publish=None)` and the pre-existing `PostPromotionSequencer.mark_projection_current` can stamp CURRENT without rows; tests only | `test_no_production_code_marks_a_projection_current_by_hand` and `..._completes_a_generation_without_publishing_it` fail on a production caller | move both to test helpers |
| Rows marked CURRENT by hand before this change (staging harness) are never rebuilt | none in production (no caller existed); staging must be re-seeded or retried | before re-certifying: retry every CURRENT instrument that has no `complete` claim |
| The `complete()` transaction retry catches only `OperationFailure`: a network-labelled transient error or an unknown commit result goes to `fail()` (FAILED, backoff, re-embed) | converges on the next pass; never CURRENT without rows | `session.with_transaction` |
| The guard tests against hand-stamped CURRENT are pattern matches (a bare enum member, a literal, a renamed receiver pass them) | the stamp only exists in the worker fence today | move `mark_projection_current` / `complete(publish=None)` to test helpers |
| Graph expansion still filters by the selected project while lexical/vector no longer do, so an organisation-scoped instrument's graph source is empty but reported `success` | graph expands evidence, never admits it | anchor graph containment on the eligible set |
| A publication hold landing between the builder's authority check and the completion commit re-inserts rows the hold purged | read-time containment still excludes the document | re-check `is_publication_blocked` inside the publication transaction |
| The release-gate test proves the lexical source; the vector test double does not run `VectorClient`'s filter semantics | payload flattening and evidence filters reviewed by hand (round 3) | pin `VectorClient.upsert` payload through `_build_filter` with the evidence filters |
| Each pass scans `contract_documents` with an unindexed `$expr`, and exhausted generations stay due | one worker, small collection | index + pre-skip exhausted/backoff claims |
| Upload ingestion still writes vectors to the configured `QDRANT_COLLECTION`; only the projection names `document_vectors` | evidence reads projection vectors, which are now always in `document_vectors` | check production `QDRANT_COLLECTION` before deploy (not read here: production is out of bounds) |

Deploy note: the fix lives in the **contract-worker** as well as the backend, so this release
is `FULL` or `BACKEND_ONLY`. Build with `RELEASE_SHA="$(git rev-parse HEAD)"` - a build argument
baked into the image, not an `up`-time variable - write the release manifest and verify against
it (deployment guide 5a-7a); `post_deploy_verify.sh` fails when any app service runs an image
the manifest does not record, or the runtime heartbeat or ownership flag disagrees.
