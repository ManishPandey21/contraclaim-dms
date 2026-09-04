# External Service Integration Tests

This directory now has two layers of coverage:

- default contract-style integration tests that run in CI without live credentials
- opt-in live smoke tests that hit real OpenAI, Qdrant, and Falkor/Redis services

Default behavior:
- contract tests run automatically
- live tests are skipped unless `RUN_EXTERNAL_INTEGRATION_TESTS=1`

What runs without external infrastructure:
- OpenAI document-processing flow through `OpenAIService` using a local fake client contract
- Qdrant vector flow through `LangChainVectorService` using `QDRANT_URL=:memory:` and deterministic local embeddings
- Falkor/Redis vector flow through `FalkorDBVectorService` using an in-test Redis double

Required environment for live tests:
- OpenAI document path: `OPENAI_API_KEY`
- Qdrant vector path: `OPENAI_API_KEY`, `QDRANT_URL`
- Qdrant containment path: `QDRANT_TEST_URL`, `QDRANT_API_KEY` (or `QDRANT_TEST_API_KEY`)
- FalkorDB graph path: `FALKOR_TEST_HOST`, `FALKOR_TEST_PORT`
- Redis queue and runtime-state path: `REDIS_TEST_URL`
- Falkor/Redis *vector* path: `FALKORDB_URL` (implements the withdrawn Gate 2
  criterion; evidence for no bullet)

Optional live-test overrides:
- `OPENAI_INTEGRATION_MODEL`
- `OPENAI_INTEGRATION_TIMEOUT`
- `QDRANT_INTEGRATION_EMBEDDING_MODEL`
- `QDRANT_INTEGRATION_VECTOR_SIZE`
- `QDRANT_INTEGRATION_DISTANCE`
- `QDRANT_INTEGRATION_TIMEOUT`

Run only the default contract integration layer:

```powershell
python -m pytest backend/rbac_backend/tests/integration/test_external_services_integration.py -q
```

Run the live smoke layer:

```powershell
$env:RUN_EXTERNAL_INTEGRATION_TESTS = "1"
$env:OPENAI_API_KEY = "..."
$env:QDRANT_URL = "http://localhost:6333"
$env:FALKORDB_URL = "redis://localhost:6380"
python -m pytest backend/rbac_backend/tests/integration/test_external_services_integration.py -m live_external_service -q
```

## Staging Gate-2 mode

`CONTRACLAIM_STAGING_GATE=1` turns this directory from a developer convenience
into gate evidence, and the two behave differently on purpose.

Several of these suites default to this machine's own containers -
`FALKOR_TEST_HOST`/`FALKOR_TEST_PORT` to `localhost:6380`, `QDRANT_TEST_URL` to
`http://127.0.0.1:6333` - and `test_qdrant_containment_live.py` falls back to
reading `config/secrets/qdrant_api_key` out of the checkout. That is what makes
them pleasant to run locally. It is also how a staging run measures the
development engines and reports the result as staging evidence, which is the
one failure a live suite must not be able to produce.

Under the flag (`backend/rbac_backend/tests/staging_gate.py`):

- every endpoint must be supplied explicitly, and none may name `localhost` or
  `127.0.0.1`;
- every live credential must come from the environment - the checkout-local
  secret is refused;
- `RUN_EXTERNAL_INTEGRATION_TESTS=1` and `ENVIRONMENT=staging` are mandatory;
- a **missing** variable fails instead of skipping, and a **skip** in one of the
  four modules Gate 2 is scored from is reported as a failure;
- the preflight runs before collection, so a misconfigured run stops with one
  status table (`PRESENT` / `ABSENT` / `INVALID SOURCE`, never a value) rather
  than a wall of import errors.

Nothing changes without the flag: the local defaults and the skips stay exactly
as they were.

Build the staging environment explicitly rather than sourcing the repository's
`.env`; an inherited developer value decides what the gate measured. The full
variable list is in `.env.staging.example`.
