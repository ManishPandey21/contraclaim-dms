# Arbitration LangGraph Pending Work and Blockers

**Original date:** 2026-07-23
**Independent re-audit:** 2026-07-24
**Scope:** Active backend, frontend, APIs, workers, migrations, infrastructure, security controls, recovery behavior, production-like drills, and global acceptance criteria in the Arbitration Pleadings Workflow and LangGraph Implementation Plan.

This document is an execution record, not an implementation-status claim. Results below were obtained by tracing active imports and routes and by running the identified code against the production image or real backing services. Test names and earlier progress summaries were not accepted as evidence by themselves.

## 1. Release decision

Primary rollout remains **fail-closed**:

- `ARBITRATION_ENGINE_DEFAULT=arbitration_v2`
- `ARBITRATION_LANGGRAPH_ROLLOUT_ENABLED=false`
- `ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=false`
- primary/canary allocation remains zero
- `arbitration_v2` remains active and is not deprecated

Production acceptance must not be issued while any item in section 4 remains open.

## 2. Corrections made by this audit

### 2.1 Authoritative LangGraph command ownership and replay safety

- Graph command nodes now own the authoritative domain mutations for generation, remediation, validation, and completion paths.
- Workflow effect keys bind replay to the exact workflow/run/node boundary.
- Generation run IDs and version IDs are deterministic at the command boundary.
- Duplicate-key recovery rehydrates the already-created authoritative run/version instead of creating or selecting an unrelated version.
- Workflow state records the exact returned generation run ID, version ID, and version hash.
- Remediation replay is deterministic and recovers the original side effect.

### 2.2 Process-kill and cancellation fencing

- A workflow execution lease is claimed, renewed, and released in MongoDB.
- Cancellation is atomic and only succeeds while the workflow has no active execution lease.
- Stale execution owners cannot continue authoritative side effects after cancellation or lease loss.
- Parallel LangGraph fan-out releases the invocation-level lease at the correct boundary and reacquires it for the following authoritative boundary.

### 2.3 Active dependency and authentication hardening

- The active production requirements were upgraded and the production backend image rebuilt.
- `python-jose` and its vulnerable `ecdsa` dependency were removed from the active runtime.
- JWT handling now uses `PyJWT[crypto]`.
- OIDC ID tokens are restricted to `RS256` or `ES256`, must include `kid`, must match exactly one JWK, and are verified for signature, audience, and issuer.
- Client dependencies were upgraded and explicit safe overrides added.
- FastAPI's lazy included-router representation is traversed by the route-security inventory, so nested effective paths remain audited after the framework upgrade.
- The contracts router now resolves `PolicyService` through an explicit dependency provider compatible with the upgraded FastAPI dependency parser.

### 2.4 Production configuration and monitoring validation

- `FALKORDB_CLEANUP_REFERENCES` is passed to the production backend and worker environment.
- A Prometheus rule-test fixture exercises all six arbitration LangGraph alerts.
- The rule file passes syntax validation and all alert expressions pass `promtool test rules`.

## 3. Real-execution evidence

| Area | Environment | Result |
|---|---|---|
| Full backend suite | Fresh production backend image | **1085 passed, 12 skipped** |
| Focused arbitration/auth/OIDC/login | Fresh production backend image | **161 passed** |
| Route authorization inventory | Fresh production backend image | **18 passed** |
| Every command node and all eight human gates | Fresh production image + real MongoDB, worker process killed at each boundary | **Passed**, 417.09 s |
| Sustained load and cancellation under load | Fresh production image + real services, 8 workers for 60 s | **Passed**, 65.28 s |
| Checkpoint TTL | Fresh production image + real MongoDB | **Passed**, 7.09 s |
| Redis queue duplicate/lease/outage and 40-job load | Real production-like Redis | **Passed** |
| S3 availability and outage | Real S3 bucket, bridge disconnected for outage then restored | Put/get/hash/delete **passed**; outage failed closed with endpoint connection error; recovery **passed** |
| Model outage and recovery | Live configured model endpoint, bridge disconnected then restored | Outage failed closed with connection error; live recovery response **passed** |
| Historical ambiguous-row audit | Production MongoDB, dry-run then safe-label mode | **0 rows found; 0 rows requiring review** |
| Client tests | Release worktree | **102 passed, 3 skipped** |
| Client production build | Release worktree | **Passed** |
| Client dependency audit | Release worktree | **0 vulnerabilities** |
| Prometheus rules | Official Prometheus `promtool` image | Syntax **passed**; all six expression tests **passed** |

The expected exception text printed by the client error-boundary test is intentional; the client test process exited zero.

## 4. Exact unresolved acceptance blockers

### B1 — Human legal acceptance is absent

Production contains no qualifying reviewed Arbitration LangGraph workflow set covering SoC, SoD, Counterclaim, and Rejoinder. No authorized legal reviewer has signed the four pleading-type legal acceptance criteria. This evidence cannot be fabricated by an implementation agent.

**Required to clear:** authorized legal reviewers must review real or formally approved golden matters for all four pleading types, including grounding, predecessor, matrix, new-matter, citation, exhibit, and export assertions.

### B2 — Independent legal and operations acceptance receipts are absent

The server enforces all global criteria and distinct legal/operations sign-off identities, but no valid acceptance receipt has been issued.

**Required to clear:** after every other criterion passes, distinct authorized legal and operations approvers must sign the current immutable evidence set.

### B3 — No production alert-notification receiver is configured

The six alert rules and their expressions pass `promtool`, but production has no deployed Prometheus/Alertmanager/Grafana receiver chain or destination. A real end-to-end delivered notification therefore cannot be exercised.

**Required to clear:** deploy an approved monitoring/notification stack and receiver, inject each alert condition, and record firing, routing, receipt, acknowledgement, and recovery.

### B4 — Provider-side credential rotation authority

The exposed AWS account-root access key was replaced with a least-privilege production application IAM user, the new S3 path was tested, and the root key was revoked. Internal application signing and metrics credentials are rotated after the final diagnostic run. Rotation of any exposed OpenAI or SMTP provider key requires administrative access at those providers and remains an owner action if those values were exposed outside their approved secret boundary.

**Required to clear:** provider administrators rotate the affected external keys and record the new key identifiers and revocation timestamps without placing secret values in logs or this repository.

### B5 — Final production/browser/rollback evidence

The committed code is deployed fail-closed, its exact commit is verified, and the isolated old-image rollback check passed. The browser exercise authenticated a temporary drafter account and reached the server-enforced Security/Privacy/Anti-Piracy Terms gate. It did not accept the terms or fabricate a legal-consent record, and therefore could not complete the reviewer account or tenant/role-isolation flow.

**Required to clear:** an authorized representative must approve acceptance of the active terms for two explicitly named disposable QA accounts, or supply two already-authorized QA accounts. Then complete the drafter/reviewer tenant-and-role-isolation browser flow and remove the temporary data.

### B6 — Final online Python advisory database re-query

The active requirements were rebuilt successfully after removing/upgrading the vulnerable packages identified by the audit. The final online advisory database re-query was unavailable because the execution environment's external-tool usage limit was reached.

**Required to clear:** run the repository's Python dependency-audit CI job or `pip-audit` against `backend/rbac_backend/requirements.txt` with current advisory data and retain the result.

## 5. Global acceptance status

| Plan §14.2 criterion | Status |
|---|---|
| 1. Phase-0 bypasses and negative authorization | Code/API tests passed; authenticated browser evidence pending B5 |
| 2. Immutable snapshots and hashes | Passed |
| 3. No raw evidence/drafts in checkpoints or ops output | Passed |
| 4. Crash/restart at every node and human gate without duplicate effects | Passed against fresh production image and real MongoDB |
| 5. Four pleading routes enforce all predecessors and gates | Code/API tests passed; human legal acceptance pending B1 |
| 6. New-matter permission receipt | Passed |
| 7. Governed export binds approved immutable version and audits | Passed |
| 8. No self-approval path | Passed |
| 9. Deterministic/auditable parallel merge | Passed, including sustained load |
| 10. Bounded transient-only retry without duplicates | Passed |
| 11. Safe immutable-snapshot v2 fallback | Passed |
| 12. Golden SoC/SoD/Counterclaim/Rejoinder legal review | **Open — B1** |
| 13. Real services, load, restore, browser | Service/load/restore passed; browser/rollback pending B5 |
| 14. Operations runbooks | Present; notification delivery exercise pending B3 |

## 6. Final deployment evidence

- Deployed commit: `512362470911927ee95a6391a50d53f51fa39f75` (`contraclaim/main`)
- Equivalent `projectDMS/main` commit: `d47ea624862c54ffaa7c75ede657dd0d5508116d`
- Backup identifier: `20260726-165848`; MongoDB and all required volumes are fresh and checksummed.
- Migration result: dry-run and apply both reported all registered migrations `skipped`, with no warnings.
- Public health/readiness: public `/health`, backend live/ready, MongoDB replica set, Redis, Qdrant, FalkorDB, ClamAV, and client health all passed.
- Rollout flag proof: `rollout=off`, `default=arbitration_v2`, `accepted=False`, `primary=0`, `canary=0`.
- Metrics authentication proof: unauthenticated `/metrics` returned `401`; the internal-token request returned `200`.
- Isolated old-image rollback proof: tagged pre-deployment backend image returned `/health/live` from a read-only, `--network none` container with no production volumes or database connectivity.
- Authenticated two-account browser proof: drafter authentication and mandatory terms gate passed; the remainder is blocked pending authorized terms consent as described in B5.
- Post-deploy S3/model-outage proof: network-isolated S3 and model calls failed closed with `EndpointConnectionError` and `APIConnectionError`; live recovery calls succeeded, and the S3 temporary object was deleted.
- Final internal credential rotation: `SECRET_KEY` and `METRICS_TOKEN` rotated after diagnostics; root `.env` mode verified `0600`; backend/worker recreated; post-rotation verification passed.

The first post-rotation verification found a stale `backend/.env` overriding the root Compose environment in release scripts. The scripts were corrected to load the legacy backend file only as fallback; the final post-deploy verification then passed with zero failures.

## 7. Acceptance disposition

The implementation is suitable for a **fail-closed production deployment** so that its inactive code path and operational controls can be reverified. It is **not suitable for primary activation, production acceptance, or `arbitration_v2` deprecation** while B1–B6 are unresolved.
