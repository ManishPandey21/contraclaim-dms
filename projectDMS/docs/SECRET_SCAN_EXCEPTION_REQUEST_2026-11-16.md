# Secret-scan exception request: two historical false positives on PR #114

Date: 2026-11-16 · Branch: `claude/zen-ptolemy-695liz` · Status: **submitted for authorised security review on 2026-11-17. Not applied.** The owner has assessed both findings as non-credentials. CI's "Secret scan" job stays red until the security reviewer approves and the change below is applied. No rule, allow-list or path exemption is changed by this request.

## What the scan reports

The PR scan (`gitleaks detect --log-opts "--no-merges --first-parent 6dd9f32^..HEAD"`, Gitleaks 8.24.3, the version the action installs) reports two findings, both under the default `generic-api-key` rule:

| Fingerprint | Value flagged | Why the rule fired |
|---|---|---|
| `1125e1e49a1811e10698720019fede6ff556d9c7:projectDMS/backend/rbac_backend/services/claim_bench.py:generic-api-key:60` | `claims.v1.head_test` | The constant it was assigned to was named `CLAIM_HEAD_TEST_PROMPT_KEY`; the rule reads a quoted value after a name ending in `_KEY` |
| `d5014c61540a7ce91ffdc967f743f01a2f7e6055:projectDMS/backend/rbac_backend/tests/integration/test_claim_arbitration_readings_mongo.py:generic-api-key:57` | `claim-private-1` | It followed the token `"CLM-SECRET-7"` on the same line; the rule keys on the word "secret" |

## Why neither is a secret

- **`claims.v1.head_test`** is the name of a prompt template, in the same naming series as `letter_drafting.v3.skill_step`. It grants nothing and is looked up in the prompt registry. It was renamed to `CLAIM_HEAD_TEST_PROMPT` in `bf85f92`. The rename does not help the scan, because the scan reads every commit on the branch and history is not rewritten.
- **`claim-private-1`** is a synthetic `_id` seeded into a disposable test database by the privacy test for the production readings reader. The test asserts this value never appears in the reader's output. `"CLM-SECRET-7"` beside it is a synthetic claim reference with the same purpose. Neither value exists outside the test file.

## Proposed change, exactly

Add one file at the repository root, `.gitleaksignore`, containing these two lines and nothing else:

```text
d5014c61540a7ce91ffdc967f743f01a2f7e6055:projectDMS/backend/rbac_backend/tests/integration/test_claim_arbitration_readings_mongo.py:generic-api-key:57
1125e1e49a1811e10698720019fede6ff556d9c7:projectDMS/backend/rbac_backend/services/claim_bench.py:generic-api-key:60
```

Why this form:

- A fingerprint names one commit, one file, one rule and one line. It exempts that occurrence only. The same value in any other commit, file or line is still reported (control 5 below).
- `.gitleaks.toml` is not changed. No rule is weakened, no value pattern is allow-listed, and no directory or path is exempted.
- Gitleaks reads `.gitleaksignore` from the scanned checkout's root by default. The action passes no option that disables it.

Not proposed: rewriting the branch's 28 commits, a value allow-list in `.gitleaks.toml` (the form `c89ad35` added and then reverted), or a path exemption.

## Forward fix already made (no security control touched)

The test's synthetic reference `"CLM-SECRET-7"` is renamed `"CLM-PRIVATE-7"`. The current files then pass a plain directory scan with no exceptions at all (results below), so only the two historical commits need the fingerprints.

## Evidence

Run locally with Gitleaks 8.24.3 in a disposable clone of the branch. The clone was deleted afterwards and nothing was pushed from it.

| # | Case | Expected | Result |
|---|---|---|---|
| 0 | Real repository, PR range, no exception (CI's command) | 2 findings, exit 2 | 2 findings (the fingerprints above), exit 2 |
| 1 | Clone, PR range, with the proposed `.gitleaksignore` | exit 0 | exit 0 |
| 2 | Control: same constant re-added in `claim_bench.py` with a random 40-character value | caught | caught, `generic-api-key`, exit 2 |
| 3 | Control: a `"CLM-SECRET-8"` tuple in the same test file with a random 32-character value | caught | caught, `generic-api-key`, exit 2 |
| 4 | Control: synthetic AWS access key id (`AKIA` + 16) in the test file | caught | caught, `aws-access-token`, exit 2 |
| 5 | Control: the exact flagged lines re-added in new commits, same files | caught (fingerprints are commit-bound) | caught, `generic-api-key`, exit 2, both lines |
| 6 | Control: synthetic GitHub token (`ghp_` + 36) in `claim_bench.py` | caught | caught, `github-pat`, exit 2 |
| 7 | Control: synthetic RSA private key block in the test file | caught | caught, `private-key`, exit 2 |
| 8 | The `CLM-SECRET-7` → `CLM-PRIVATE-7` rename commit alone | no finding | exit 0 |
| 9 | Current `claim_bench.py` and the current test file, directory scan (`--no-git`), no exceptions | no finding | exit 0, both |

Every synthetic credential in the controls was generated at random inside the disposable clone. None was written to this repository.

## How an owner applies it

1. Review the two values above at their commits (`git show 1125e1e:projectDMS/backend/rbac_backend/services/claim_bench.py | sed -n 60p` and `git show d5014c6:projectDMS/backend/rbac_backend/tests/integration/test_claim_arbitration_readings_mongo.py | sed -n 57p`).
2. Create `.gitleaksignore` with exactly the two lines above, commit it, and push to the branch.
3. Confirm the next CI run's "Secret scan" job reports `no leaks found`.
4. Record the approval here: name, date and commit.

| Date | Owner | Decision | Commit |
|---|---|---|---|
| 2026-11-17 | Owner | Both findings assessed as non-credentials; fingerprint-only exception submitted for authorised security review | — |
| — | Security reviewer | Pending | — |
