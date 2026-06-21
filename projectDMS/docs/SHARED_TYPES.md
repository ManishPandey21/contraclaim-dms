# Shared API types (generated from OpenAPI)

To reduce client/server payload drift, the frontend can consume TypeScript types
generated from the backend's OpenAPI schema instead of hand-redeclaring request /
response interfaces in each `*-api.ts` service.

This is wired as a two-step, reproducible pipeline. The generated artifacts are
**not** committed (they are environment-derived); generate them on demand.

## Regenerate

From the repo root:

```bash
# 1. Dump the backend schema -> client/openapi.json (no server / DB needed)
python backend/scripts/dump_openapi.py

# 2. Generate TS types -> client/src/types/api.generated.ts
cd client
npm run gen:api-types
```

`gen:api-types` runs `npx -y openapi-typescript openapi.json -o
src/types/api.generated.ts`, so it needs no committed dev-dependency and does not
affect `package-lock.json` / `npm ci`.

## Use

Import the generated `paths` / `components` from `src/types/api.generated.ts` in
the service layer, e.g. type a response as
`components["schemas"]["BankGuarantee"]` rather than a hand-written interface.
Adopt incrementally — start with the highest-churn payloads — and keep the
hand-written DTOs only where the generated shape is awkward.

## When to regenerate

Re-run the pipeline whenever backend models or routes change (ideally in CI as a
drift check: regenerate and fail if `git diff` is non-empty).
