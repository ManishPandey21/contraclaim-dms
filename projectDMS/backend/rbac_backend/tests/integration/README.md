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
- Falkor/Redis vector path: `FALKORDB_URL`

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
