# Two-User Tenant-Isolation Verification — 2026-08-02

**Conclusion: `BLOCKED`.** The two-user gate was not executed. The host provided
for verification is the production environment, not staging, and it holds real
customer data. No authenticated test was run, so the gate remains unproven.

---

## 1. Environment identification

Commands run over `ssh contraclaim` (read-only; no secret values printed):

```bash
hostname
docker ps --format "{{.Names}}\t{{.Status}}"
docker exec contraclaim-backend-1 sh -c 'echo ENVIRONMENT=$ENVIRONMENT; echo ALLOW_DEV_HEADERS=$ALLOW_DEV_HEADERS'
docker ps -a --format '{{.Label "com.docker.compose.project"}}' | sort -u
ls -d /opt/*staging* /opt/*stage* /opt/*test*
curl -s -o /dev/null -w "%{http_code}" https://contraclaim.com/
```

| Observation | Value | Bearing on the task |
|---|---|---|
| `ENVIRONMENT` | `production` | Host is not staging |
| Public site | `https://contraclaim.com/` → **HTTP 200** | Live and serving users |
| Compose projects on host | `contraclaim` only | No separate staging stack |
| Staging directories | none found | No staging deployment |
| `ALLOW_DEV_HEADERS` | `false` | No test-login bypass available |

The task specified *"the ContraClaim **staging** server"* and *"Do not use
production accounts or production data."* Those two instructions cannot both be
satisfied on this host.

## 2. Data classification

```bash
docker exec contraclaim-mongo1-1 mongosh --quiet /tmp/u.js   # inventory script, emails masked
```

9 active users, 6 organisations, 5 projects. Two distinct populations:

| Population | Character | Usable for this test? |
|---|---|---|
| `*@example.com` (6 users) | Seeded test accounts | Yes, if credentials existed |
| `*@draipl.com` (2 users) | **Real customer organisation** | No — production data |
| `*@contraclaim.com` (1 user) | Real staff superadmin | No — production account |

Candidate pair had credentials been available (both seeded, different
organisations, one project each):

| Masked user | Role | Organisation | Projects |
|---|---|---|---|
| `or***@example.com` | organization-admin | `c06c95b2-…` | 1 |
| `he***@example.com` | organization-admin | `7c0dead3-…` | 1 |

A cross-project pair also exists within `org2` (`co***`, `do***`, both
`projectuser`).

No credential material was read, printed, exported, or altered. Password hashes
were not accessed or examined.

## 3. Why the gate could not be executed

All 9 accounts store password hashes only. The four possible routes to an
authenticated session were each unavailable or prohibited:

| Route | Status |
|---|---|
| Existing plaintext test credentials | None found in repo, seed script, or environment |
| Dev-header test login | `ALLOW_DEV_HEADERS=false` |
| Reverse a password hash | Prohibited by the task, and infeasible |
| Reset an existing account's password | Would modify credentials on a live production account, locking out its real user |
| Create temporary users | Authorised only *"on staging"*; this host is production with live customer data |

Creating accounts here would place new records in a production database serving
real customers. That is a permanent change to production data and outside the
authorisation given, so it was not done.

## 4. Test results

| # | Scenario | Expected | Actual | Status |
|---|---|---|---|---|
| 1 | Header shows no cross-tenant organisation | Only own org listed | Not executed | `BLOCKED` |
| 2 | Direct URL to other user's project | Rejected / not adopted as context | Not executed | `BLOCKED` |
| 3 | Forged `localStorage` org/project ids | Discarded on revalidation | Not executed | `BLOCKED` |
| 4 | API `?organization_id=<other>` | 4xx or zero rows | Not executed | `BLOCKED` |
| 5 | Copied document / letter ids | 4xx | Not executed | `BLOCKED` |
| 6 | File download by foreign document id | 4xx | Not executed | `BLOCKED` |
| 7 | Search returns no foreign-org rows | Empty of foreign org | Not executed | `BLOCKED` |
| 8 | Context survives refresh | Unchanged, own scope | Not executed | `BLOCKED` |
| 9 | Login as second user | No inherited scope | Not executed | `BLOCKED` |
| 10 | Project switch clears previous rows | Previous content gone | Not executed | `BLOCKED` |

The automated specs for all ten exist at
`client/e2e/tenant-isolation.spec.ts` and were confirmed to collect and skip
cleanly (10 collected, 10 skipped, no credentials set). Collection is **not**
evidence of isolation.

## 5. What unblocks this

Any one of the following, in order of preference:

1. **A staging deployment** with seeded data and two known test logins. Point
   the suite at it:
   ```bash
   RUN_TENANT_ISOLATION_E2E=1 E2E_BASE_URL=https://staging.example \
   E2E_USER_A_EMAIL=… E2E_USER_A_PASSWORD=… \
   E2E_USER_B_EMAIL=… E2E_USER_B_PASSWORD=… \
   npm run test:e2e:isolation
   ```
2. **Plaintext credentials for two `@example.com` seeded accounts**, if they are
   recorded elsewhere. No database change required; the accounts already exist
   in separate organisations.
3. **Explicit written authorisation** to create two temporary users in this
   production database, with agreed cleanup. Only the seeded `@example.com`
   organisations would be touched; `@draipl.com` data would not be read or
   modified.

Option 3 is not recommended while the alternatives are open.

## 6. Constraint compliance

| Constraint | Adhered |
|---|---|
| No credentials created, modified, reset, or deleted | Yes |
| No passwords, hashes, tokens, connection strings, or keys printed | Yes |
| No hash reversal attempted | Yes |
| No production accounts or data used for testing | Yes — verification stopped instead |
| No application code modified | Yes |
| No permanent database changes | Yes — all queries read-only |
| Usernames masked | Yes |

Two temporary read-only script files (`/tmp/inv.js`, `/tmp/u.js`) were written
to the host and the mongo container to run the inventory queries, and removed
afterwards. No other artefacts were left.

---

**`BLOCKED`: Complete verification could not be performed because the host
supplied is the production environment rather than staging and holds live
customer data, and no usable test credentials exist. The two-user gate must not
be considered `PASS` until the blockers above are resolved and the suite is
rerun.**
