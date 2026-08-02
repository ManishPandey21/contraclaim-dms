# Two-user tenant-isolation tests

`tenant-isolation.spec.ts` proves that one signed-in user cannot reach another
user's Organisation or Project. It runs against a **real backend with real
logins** — deliberately not mocked, because mocking the API would only prove the
frontend renders what it is handed, which is the claim under test.

It is skipped by default. Everything below is what "unblocks" it.

## What you need

Two accounts in **different organisations**, each able to sign in and reach at
least one project.

| Variable | Required | Purpose |
|---|---|---|
| `RUN_TENANT_ISOLATION_E2E=1` | yes | Master switch |
| `E2E_USER_A_EMAIL` / `E2E_USER_A_PASSWORD` | yes | User in organisation A |
| `E2E_USER_B_EMAIL` / `E2E_USER_B_PASSWORD` | yes | User in organisation **B** |
| `E2E_BASE_URL` | no | Defaults to `http://127.0.0.1:5173` |
| `E2E_B_ORG_ID` | no | Enables API-scope and search-isolation tests |
| `E2E_B_PROJECT_ID` | no | Enables direct-URL and forged-storage tests |
| `E2E_B_DOCUMENT_ID` | no | Enables document and download direct-reference tests |
| `E2E_B_LETTER_ID` | no | Enables letter direct-reference test |

The optional ids must belong to **user B only**. Without them the corresponding
tests skip individually rather than passing vacuously — a skip is reported, not
hidden.

## Running

```bash
RUN_TENANT_ISOLATION_E2E=1 \
E2E_USER_A_EMAIL=a@example.com E2E_USER_A_PASSWORD='...' \
E2E_USER_B_EMAIL=b@example.com E2E_USER_B_PASSWORD='...' \
E2E_B_ORG_ID=... E2E_B_PROJECT_ID=... E2E_B_DOCUMENT_ID=... \
npm run test:e2e:isolation
```

Point `E2E_BASE_URL` at a staging deployment to run without a local dev server.

## What each test proves

| Test | Proves |
|---|---|
| each user sees only their own organisation | No cross-tenant organisation appears in the header selector |
| direct URL to B's project | A URL-supplied project id cannot become the active context |
| forged local-storage context | Writing B's ids into `localStorage` is discarded on revalidation |
| API refused B's organisation | `?organization_id=<B>` is refused, or returns zero rows |
| copied document / letter ids | Direct object references from B do not resolve for A |
| file download by id | Possessing a document id does not grant the file |
| search isolation | No result carries B's organisation id |
| context survives refresh | Scope is stable and still the user's own |
| login as the other user | A fresh login does not inherit the previous user's scope |
| project switch | The previous project's content is gone after switching |

A cross-tenant request must either be refused (`400/401/403/404/409/422`) or
return an empty result. A `200` carrying rows fails the test.

## Caveats

* These tests **write no data**. They only read. Safe against staging; do not
  point them at production without agreement.
* `logging in as the other user does not inherit the previous context` clears
  `localStorage` between logins. Run it against accounts whose stored UI
  preferences you do not mind losing.
* The project-switch test needs user A to have at least two selectable
  projects, otherwise it skips.
