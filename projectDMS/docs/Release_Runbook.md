# Release Runbook

## Purpose

This runbook defines the release discipline required before production deployment.

## CI Gates

Every pull request and push to `main` or `master` must pass:

- Secret scan with Gitleaks.
- Backend pre-commit hooks.
- Backend compile check.
- Backend test suite.
- Frontend lint.
- Frontend Vitest test run.
- Frontend production build.
- Python dependency vulnerability scan.
- npm dependency vulnerability scan.
- Backend Docker image build.
- Frontend Docker image build.
- Container image vulnerability scan for high/critical issues.

## Image Tagging

Images must be tagged by commit SHA:

```text
projectdms-backend:<git-sha>
projectdms-client:<git-sha>
```

Do not deploy mutable tags like `latest` to production.

## Staging Promotion

1. Merge only after CI passes.
2. Build immutable images from the merge commit.
3. Deploy the SHA-tagged images to staging.
4. Run smoke tests:

```bash
SMOKE_BASE_URL=https://staging.example.com python scripts/smoke_health.py
```

5. Exercise one authenticated smoke workflow:
   - Login.
   - List documents.
   - Upload a small document.
   - Create a contract upload session.

## Production Promotion

1. Confirm staging smoke tests passed.
2. Confirm database migrations/backfills are backward compatible.
3. Deploy using rolling or blue/green strategy.
4. Monitor readiness, error rate, latency, and queue depth.
5. Keep the previous image tag available for rollback.

## Rollback

Rollback trigger examples:

- Readiness probe failures.
- Elevated 5xx rate.
- Upload failures.
- Queue backlog grows without recovery.
- Critical frontend route fails.

Rollback steps:

1. Redeploy previous backend and frontend image tags.
2. Confirm `/health/ready` passes.
3. Run `scripts/smoke_health.py`.
4. Verify document listing and upload manually in staging/production as appropriate.
5. Leave backward-compatible migrations in place unless a specific rollback migration is available and tested.

## Post-Release Checks

- Error rate stable.
- P95 latency stable.
- Upload success rate stable.
- Contract queue depth drains.
- No new high-severity exceptions.
- No new failed health checks.
