# Manual And Legacy Validation

This directory contains developer-run scripts and archived reference material that are intentionally excluded from automated pytest runs.

Use this area for:
- one-off environment verification
- local smoke scripts against a running server
- historical notes retained for troubleshooting

Do not import production code from `manual/`.

Automated tests live under `backend/rbac_backend/tests`.
Real service integration tests live under `backend/rbac_backend/tests/integration`.
