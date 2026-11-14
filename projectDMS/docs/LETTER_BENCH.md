# Letter Bench and the skill registry

Operator and developer notes for skill-based letter drafting. The design is in
the Phase 1 plan ("The Letter Bench"); this file records what was built and how
to run it.

## What it is

- **Skill registry.** A skill is a versioned method document (`SKILL.md` plus
  reference notes) with a manifest we write. Three system skills ship at 1.0.0:
  `indian-infra-letter-analysis`, `indian-infra-contract-letters`,
  `indian-infra-variations`. Their text is in
  `backend/rbac_backend/initial_data/skills/`.
- **Letter Bench.** One screen at `/letters/:id/bench`. The incoming letter is
  dissected into assertions, skills are routed per assertion, the user sets a
  position on each, positions are locked, and the reply is composed one
  paragraph per assertion.

Locking positions writes an ordinary accepted `StrategyVersion`. Composing
writes an ordinary `DraftRun` through `DraftRunService.create_run`. Review,
approval, export and issue are unchanged.

## Switching it on

| Variable | Values | Default |
| --- | --- | --- |
| `LETTER_BENCH_ROLLOUT_MODE` | `off`, `canary`, `primary` | `off` |
| `LETTER_BENCH_CANARY_TENANT_IDS` | comma-separated organisation ids | empty |
| `VITE_LETTER_BENCH_ENABLED` (client build) | `false` hides the Bench | unset |

The server decides. With the mode `off` every Bench route answers 404 and the
client uses the earlier screens. Both variables are passed to the backend in
`docker-compose.prod.yml`.

**Rollback:** set `LETTER_BENCH_ROLLOUT_MODE=off` and restart the backend.
Letters started on the Bench keep their strategy versions, runs and drafts, and
open in the earlier screens.

## Migrations

Run the dry run first, inside the backend container:

```bash
python -m rbac_backend.scripts.migrate_database --list
```

```bash
python -m rbac_backend.scripts.migrate_database --fail-on-warning
```

| Version | What it does |
| --- | --- |
| `20261114_0001` | Indexes for `skills`, `skill_versions`, `skill_bindings` |
| `20261114_0002` | Seeds the three system skills as published 1.0.0 |
| `20261114_0003` | Indexes for `letter_position_boards`, `letter_skill_steps` |

The registry falls back to the bundled packs when the collections are empty,
so the Bench works before `0002` runs; the indexes in `0001` and `0003` should
be applied before the Bench is switched on, because idempotent open and step
replay rely on their unique indexes.

`0002` never overwrites a stored version. If the bundled text of a published
version changes without a version bump it reports a warning, which fails
`--fail-on-warning`.

## Permissions

| Permission | Who | Used for |
| --- | --- | --- |
| `drafting.skill.read` | roles granted it, within their own scope | the registry routes, including full skill text |
| `drafting.skill.publish` | holders of `drafting.admin` | import, publish, deprecate |
| `drafting.skill.bind` | holders of `drafting.admin` | pin or disable per organisation or project |

Drafters do not need `drafting.skill.read`. The Bench lists the skills that can
be applied on a letter through `GET /api/letters/{id}/bench/skills`, which is
authorised by access to that letter and returns names, versions and
availability only, never skill text.

Opening the Bench needs read access to the document and
`drafting.request.create` in its scope. The first analysis is part of opening
and runs under that authorisation; running it again needs
`drafting.draft.create`.

Other Bench routes use the existing letter permissions: letter read access to view,
`drafting.draft.create` to edit and run skills, `drafting.request.accept` to
lock positions (the same permission as accepting a plan), `drafting.draft.edit`
to edit a reply paragraph.

## Publishing a skill version

1. Import the zip as a draft: Skills page, or
   `python -m rbac_backend.scripts.import_skill_bundle pack.zip --version 1.1.0 --kind drafting --manifest manifest.json`.
2. Run the golden-set evaluation and record its result when publishing. A
   version below its threshold, with no cases, or with text flagged by the
   guardrail scan cannot be published.
3. Published versions are write-once. A correction is a new version.
4. Pin a version to a project with `PUT /api/skills/bindings` if it must not
   move when a newer version is published.

Every skill step and every run records the skill key, version and content hash
it used, so publishing never changes what a past run records.

## Failure behaviour

- **Language model unavailable.** Each step falls back to a deterministic
  result and is marked degraded: analysis uses rule-based extraction, a
  variation evaluation stops at "insufficient information" and lists what is
  missing, a reply paragraph uses template wording with `[CONFIRM: ...]`
  placeholders. A draft built from degraded steps is saved as
  `needs_attention`, never `completed`.
- **A step is interrupted.** A board left "running" for 15 minutes is marked
  failed with a visible message the next time it is read.
- **Sources.** Skill text is method, not evidence. It never enters the source
  ledger, and the existing unsupported-clause validator still decides whether
  a clause citation is allowed.

## Not built in this phase

- The LangGraph v3 engine is unchanged; the Bench runs its own skill steps and
  hands the composed letter to the v2 run pipeline.
- Shadow comparison against v2 on live requests.
- A golden evaluation set (the publish gate exists; the letters do not).
- Starting a fresh letter from a key date or hindrance record (a variation
  record is supported).
- A reply-deadline column and sort on the letters table.
- Attaching documents to an assertion's "needs" list.
- The reply editor is plain text per paragraph, not the Tiptap editor, because
  the validators and the Word/PDF export work on plain text.
