# Launch Blocker Resolution Runbook

Companion to `docs/PRODUCTION_READINESS_RELEASE_GATE.md`. For each critical
blocker this records what has been resolved in code, and the exact commands an
operator must run in a real environment (staging/CI/cloud) to capture the
remaining evidence. Score is computed by `python scripts/production_readiness_score.py`
from the gate checkboxes; only check a box once its evidence genuinely exists.

Current score: **38/100** (target 85). Verdict: **Not Ready**.

| Blocker | Code-side | Remaining (needs environment) |
| --- | --- | --- |
| P0-007 dependency scan | `requests` → 2.32.4 (CVE-2024-47081) | Framework/AI-stack upgrade + `pip-audit` green |
| P0-002 live integrations | tests exist, gated by env flag | staging run with real services |
| P0-005 upload antivirus | enforced + tested (Gate 5: 3/5) | live ClamAV scan in staging |
| P0-006 Org-Admin permissions | HTTP-boundary regression added | manual staging validation |
| P0-003 browser E2E | contract flow covered | auth/perms/chronology/arbitration specs + live run |
| P0-008 deploy/backup/restore | scripts exist | execute drills + sign-off |

---

## P0-007 — Python dependency scan (Gate 1)

Resolved in code: `backend/rbac_backend/requirements.txt` pins `requests==2.32.4`.

Remaining (run in an env with `pip-audit` + network):

```bash
python -m pip install --upgrade pip pip-audit
pip-audit -r backend/rbac_backend/requirements.txt
```

The bulk of the red findings are in the FastAPI/Starlette and
LangChain/LangGraph/Pydantic-AI families and require a coordinated upgrade
followed by `pytest backend/rbac_backend/tests -q`. `ecdsa` (CVE-2024-23342,
Minerva) is an upstream **won't-fix** timing side-channel pulled transitively by
`python-jose`; the app signs/verifies JWTs with HS256 (`ALGORITHM=HS256`), so the
EC path is unused. If it remains the only finding, document the acceptance and
pin it in the `pip-audit` ignore list with this justification. Then check the
Gate 1 GitHub Actions box once the remote run is green.

## P0-002 — Live integration baseline (Gate 2)

```bash
# In staging, with real OPENAI_API_KEY, QDRANT_URL/API_KEY, FALKORDB_URL/PASSWORD,
# APP_REDIS_URL and all required live env vars exported:
RUN_EXTERNAL_INTEGRATION_TESTS=1 \
  pytest backend/rbac_backend/tests/integration/test_external_services_integration.py -v
```

Record the env var names used (not values) and the pass output, then check the
five Gate 2 boxes.

## P0-005 — Upload and content safety (Gate 5: 3/5 done)

Done in code: production refuses to boot unless antivirus is enabled and
fail-closed; uploads reject not-clean files. Remaining for full Gate 5:

```bash
# Staging with ClamAV reachable:
#  1) upload a clean file  -> accepted
#  2) upload an EICAR test file -> rejected HTTP 400
#  3) stop clamav, upload -> rejected HTTP 400 (fail closed)
```

The two unchecked boxes are: explicit MIME/extension/size/concurrency validation
evidence, and confirmation that sensitive extracted text is not logged.

## P0-006 — Org-Admin permissions (Gate 3 + Gate 4)

Done in code: `backend/rbac_backend/tests/test_org_admin_permissions_api.py`
reproduces the catalog-missing Client DMS permission retrieve over
`GET /api/roles/{id}/permissions`. Remaining: manual save/retrieve in staging as
an Org-Admin (Gate 4 box) and the Org-Admin permission browser E2E (Gate 3 box).

## P0-003 — Browser E2E (Gate 3)

Add Playwright specs under `client/e2e/` mirroring `contract-workflows.spec.ts`
for: login/logout/session/CSRF; Org-Admin permission save/retrieve; document
upload/view/download/share/denial; timeline verify/reject; chronology
create/extract/verify/export; arbitration create/generate/export/approve; and
empty/loading/error states. Run:

```bash
cd client && npm run test:e2e
```

## P0-008 — Deploy, backup, restore, sign-off (Gates 7, 8, 9)

```bash
bash scripts/pre_deploy_readiness.sh           # Gate 7
python scripts/preflight.py
docker compose --env-file .env -f docker-compose.yml -f docker-compose.prod.yml config
bash scripts/deploy.sh                          # staging deploy
python scripts/smoke_health.py                  # Gate 9 smoke after deploy
bash scripts/production_backup.sh               # Gate 8
bash scripts/backup_offsite_s3.sh
bash scripts/mongo_restore.sh <artifact>        # restore drill into isolated env
python scripts/smoke_health.py                  # smoke after restore
python scripts/production_readiness_score.py    # confirm >= 85
```

Record RPO/RTO and obtain release-owner sign-off (final Gate 9 box). Bash scripts
must be syntax-checked on a Linux/WSL host (`bash -n scripts/*.sh`) — this could
not run on the Windows dev host.
