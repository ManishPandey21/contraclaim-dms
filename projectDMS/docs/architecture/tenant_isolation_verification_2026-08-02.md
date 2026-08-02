# Two-User Tenant-Isolation Verification — 2026-08-02/03

**Conclusion: `PARTIAL`.** Server-side isolation is `PASS` — proven by seven
authenticated two-user API tests against the live deployment, with a positive
control. Browser-UI isolation is `BLOCKED`: the deployed frontend does not
contain the code under test.

Run under explicit authorisation to treat the production host as staging.

---

## 1. Environment

| Property | Value |
|---|---|
| Host | `contraclaim` (`vps-5dec80c1`) |
| `ENVIRONMENT` | `production` — **not** staging |
| Public site | `https://contraclaim.com/` HTTP 200 |
| Staging stack | none — single compose project |
| `ALLOW_DEV_HEADERS` | `false` |
| Deployed backend | built 2026-07-30, commit `d36a2e5` (≡ `9a29dd5`) |
| Deployed frontend | built 2026-07-31 from `main` |

The host was raised as production; the user authorised proceeding and treating
it as staging.

## 2. Test accounts

Two temporary accounts created, used, and deleted. Real accounts were not
modified, and the `@draipl.com` customer organisation was never read or touched.

| Account | Role | Organisation | Project |
|---|---|---|---|
| `tmpverify.a***` | `orgadmin` | `7c0dead3-…` (KPIL-GULERMARK JV) | `668e61e9-…` KNPCC-11 |
| `tmpverify.b***` | `orgadmin` | `c06c95b2-…` (GULERMAK-SAM JV) | `6d7c545c-…` GUL-SAM JV KNPCC05 |

Password generated inside the backend container, captured to a local `600` file,
never printed, and destroyed afterwards. Hashing used the application's own
`get_password_hash`. No hashes were read or reversed.

## 3. Two false results found and corrected during verification

Both would have produced a fraudulent `PASS`.

**3.1 Wrong login endpoint.** The spec posted to `/login`, a *frontend* route
that returns the SPA shell with HTTP 200 for any input — including a nonexistent
user and a wrong password. The real endpoint is `/api/login` (auth router is
mounted at `/api`), which correctly returns 401. The first run's "6 passed" was
therefore produced by *unauthenticated* sessions, where every cross-tenant check
passes on a 401.

Fixed: `apiSession()` now posts to `/api/login`, then calls `/api/me` and
asserts the returned email matches, so a test run cannot proceed without a
provably real session.

**3.2 Sessions with no permissions.** The temp users were created with role
`organization-admin` (the alias real users carry). `PermissionService` resolves
roles with `db.roles.find_one({"_id": <raw role string>})`, and the `roles`
collection is keyed by canonical ids (`orgadmin`). The alias never matched, so
the accounts held **zero** permissions and every request returned
403 — again passing all isolation assertions vacuously.

Fixed for the test by using the canonical `orgadmin` id and flushing the
`user_perms:*` Redis cache. A permanent **positive control** was added to the
spec: it asserts the session can read its own organisation's data and returns at
least one row, failing loudly if the suite is ever again satisfied by a session
that can read nothing.

## 4. Results — server-side API isolation

Command (secrets omitted):

```bash
RUN_TENANT_ISOLATION_E2E=1 E2E_BASE_URL=https://contraclaim.com \
E2E_USER_A_EMAIL=… E2E_USER_B_EMAIL=… \
E2E_B_ORG_ID=c06c95b2-… E2E_B_PROJECT_ID=6d7c545c-… \
E2E_B_DOCUMENT_ID=6a4f4ca1b5be7b636f415b4c \
npx playwright test e2e/tenant-isolation.spec.ts -g "API isolation"
```

`7 passed (16.3s)` — 2026-08-03.

| # | Scenario | Expected | Actual | Status |
|---|---|---|---|---|
| 0 | **Positive control** — session reads own organisation | ≥1 row, HTTP 200 | Own project returned | `PASS` |
| 1 | Letters for the other organisation | 4xx or 0 rows | 0 rows | `PASS` |
| 2 | Copied document / letter id | 4xx | 403 | `PASS` |
| 3 | Download by foreign document id | 4xx | 403 | `PASS` |
| 4 | Search filtered to other organisation | no foreign rows | none | `PASS` |
| 5 | Projects listing for other organisation | no foreign rows | `[]` | `PASS` |
| 6 | Tasks for other organisation | 4xx or 0 rows | 0 rows | `PASS` |

Manual control pair, same session, confirming the result is discriminating:

```
user B, own organisation     -> 200, "GUL-SAM JV KNPCC05" returned
user B, foreign organisation -> 200, []
```

## 5. Results — browser UI isolation

`BLOCKED`. The deployed bundle contains neither `Selected organisation and
project` nor `Select organisation`; the client image was built from `main`,
which does not carry the TenantContext / TenantScopeBar / TenantContextGate work
(branch-only, per the decision not to merge to `main`).

| # | Scenario | Status |
|---|---|---|
| 7 | Header shows no cross-tenant organisation | `BLOCKED` |
| 8 | Direct URL to other user's project | `BLOCKED` |
| 9 | Forged `localStorage` context | `BLOCKED` |
| 10 | Context survives refresh | `BLOCKED` |
| 11 | Login as second user does not inherit scope | `BLOCKED` |
| 12 | Project switch clears previous rows | `BLOCKED` |

Unblocked by deploying the branch to an environment the suite can reach; the
specs then run unchanged.

## 6. Incidental finding — not part of this scope

`PermissionService.user_has_permission` looks up roles by raw string against a
collection keyed by canonical id. Any user whose `roles` array holds an alias
therefore resolves to **zero permissions**.

Present in production data: 3 of 9 users hold `organization-admin` (2) or
`project-admin` (1), none of which exist as `roles._id`. Those accounts should
be unable to perform permissioned actions. This is an availability defect rather
than a data-exposure one, and it is pre-existing — not introduced here. Worth a
separate look; it was not changed.

## 7. Constraint compliance

| Constraint | Adhered |
|---|---|
| Existing credentials not created, modified, reset, or deleted | Yes — only the two temporary accounts |
| No passwords, hashes, tokens, connection strings, keys printed | Yes |
| No hash reversal | Yes |
| Production customer data not used | Yes — `@draipl.com` never read or touched |
| Application code not modified | Yes — only the test spec was corrected |
| Permanent database changes avoided | Yes — temp accounts deleted, counts restored to 9/6/5 |
| Separate authenticated sessions per user | Yes — independent API contexts |
| Usernames masked | Yes |
| Temporary credentials removed | Yes — accounts deleted, login now 401, local file destroyed |

---

**`PARTIAL`: Server-side cross-organisation isolation is verified by seven
authenticated two-user tests against the live deployment, including a positive
control that makes vacuous passes detectable. Browser-UI isolation remains
`BLOCKED` until the branch frontend is deployed. The overall gate is not `PASS`.**
