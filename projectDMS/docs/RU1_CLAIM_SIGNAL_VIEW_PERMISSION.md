# RU-1: a project-scoped `dms.claim.signal.view` permission (design)

Owner decision 2026-11-17: design a dedicated permission for claim signals. Do not
widen `drafting.request.view`; do not expose restricted letters or drafting content.
**This is a design. Nothing is built, and no role is changed.**

## What happens today (read from the code)

`GET /api/claims/bench/signals` (`routers/claim_bench.py: list_claim_signals`):

- gate: `dms.claim.view` on the selected project;
- hindrance signals: included only if the caller holds `dms.hindrance.view` there;
- Letter Bench signals: included only if the caller holds `drafting.request.view`
  there, which a tenant role reaches only through a drafting-expert allocation.

So an Organization or Project Admin sees hindrance signals but not letter signals.
Raising a potential claim from a letter signal (`POST /api/claims/bench/potential`)
also checks `drafting.request.view` on the board.

A letter signal is derived from a Letter Bench board (`services/claim_signals.py:
_board_signal`) and carries: `label` "Letter {letter_no}", `title` = the incoming
letter's **subject**, `reason` (a fixed sentence: the letter was read as raising a
claim, or a delay), `claim_type`, `event_date` = the letter's date, and the
organisation and project. It carries nothing of the draft, the chain, a skill's output
or the letter's text.

## Design

### 1. The permission

- Name `dms.claim.signal.view`; constant `Permissions.CLAIM_SIGNAL_VIEW` in
  `core/permissions.py`; added to the catalogue with the other `dms.claim.*`.
- **Project-scoped**, checked like every claim permission: `PolicyService` against the
  signal's own organisation and project (the gate answers membership;
  `build_scope_query` bounds the rows), never against the navbar selection alone.
- **Explicit grant only** (the 4B-10 rule): not inherited from `dms_admin`, a wildcard
  or Super Admin; `user_has_explicit_permission` decides it.
- **Granted to no role by default.** The seed and migration add the catalogue entry
  only; an organisation grants it to the roles that should see signals. Which seed
  roles should get it is an owner decision (see 6).

### 2. What it shows

With `dms.claim.signal.view` (and `dms.claim.view`) a caller sees, for a letter signal:
`label` (the letter number), `reason`, `claim_type`, `event_date`, the project, and
the notice clock. Holding `drafting.request.view` changes nothing about the signal
list; it remains the permission for the Letter Bench itself.

The **subject line** (`title`) is metadata of the letter, not of the board. It is shown
only if the caller may read **that document** by the document rules (document ACL,
classification, and any restriction 4B-27 adds). Otherwise the signal is shown with a
neutral title ("Incoming letter {letter_no}"), so its existence and clock are visible
without its subject. If the caller may not read even the letter's number (a restricted
document), the signal is **left out**, as a source the caller may not read is today.

Never exposed through a signal: the letter's text, the draft or its versions, the skill
chain or its outputs, reviewer names, or any board field other than those above. A
signal is a pointer; opening the letter goes through the document routes and their own
checks.

### 3. Raising a claim from a letter signal

`POST /api/claims/bench/potential` for a letter signal: replace the
`drafting.request.view` check with `dms.claim.signal.view` on the board's project
**plus** the document check on the letter (the same as 2), plus `dms.claim.create`
(unchanged). The created claim's origin keeps the letter's reference only; the
subject is copied into the claim title only if the caller may read the letter.

### 4. Hindrance signals

Unchanged: they need `dms.hindrance.view`. Folding them under the new permission would
widen hindrance access, which is not asked for.

### 5. Proof required before release (the tests to write)

- Real-MongoDB route tests: (a) a Project Admin with `dms.claim.signal.view` sees the
  letter signal of their project, not another project's; (b) without it, sees none;
  (c) with it but unable to read the letter document: a neutral title, or no signal for
  a restricted document; the subject string absent from the raw JSON; (d) holding only
  `drafting.request.view` no longer shows signals; (e) Super Admin and `dms_admin`
  without an explicit grant see none; (f) raising a potential claim with the
  permission but without document access is refused.
- A source-tree scan that `ClaimSignal` gains no field drawn from the board beyond the
  list in 2.
- Route inventory regenerated; plain-language messages for any new refusal code.

### 6. Owner decisions needed

1. Which seed roles receive `dms.claim.signal.view` by default, if any (proposal: none;
   organisations grant it).
2. Whether the neutral title in 2 is acceptable, or a signal without document access is
   always left out.
3. Release order relative to 4B-27 (restricted-document enforcement), since 2 relies on
   the document check that 4B-27 completes.

### 7. Migration

A catalogue-only migration (adds the permission, changes no role), with `notices=` for
its documentary note, dry-run first, and a fresh-install proof like the other claim
permissions. Nothing changes for any user until a role is granted the permission.
