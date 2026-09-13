# Image freshness and CVE policy

**Status: OWNER DECISION, made 2026-09-13 for the release programme (phase
R-A8W). Durable — it is not scoped to one window.**

## Why

An image that scanned clean yesterday is not an image that scans clean today.
R-A8V proved it: the exact CI Trivy policy went red **three times in one day**
on Dockerfiles that had not changed, because the vulnerability database moved —
backend 5 HIGH, client 2 HIGH, langgraph 12 including three CRITICAL, every one
already fixed upstream. F-A8V-4's `apt-get upgrade` absorbs such a wave *at the
next build*; it does nothing for an image already built and waiting for a
maintenance window.

So "the images are clean" is only ever a statement about an instant, and this
policy says which instants count.

## The policy

| # | Requirement | Enforced by |
|---|---|---|
| 1 | The full image pipeline — every image rebuilt and scanned — re-runs **weekly** on a schedule, and can be started by hand | `.github/workflows/ci.yml` `schedule` (`23 2 * * 1`, Mondays 02:23 UTC) + `workflow_dispatch`; `test_ci_static_gates.py::test_the_image_scan_reruns_on_a_weekly_schedule_and_on_demand` |
| 2 | Every build uses the **current upstream base** (`docker build --pull`), never a cached base layer | `ci.yml` build steps; `test_every_image_build_pulls_the_current_base` |
| 3 | **No automatic production deployment.** The scheduled run builds and scans; it pushes nothing and deploys nothing | `test_the_workflow_can_neither_push_an_image_nor_deploy` |
| 4 | A changed image, source or digest reaches staging or production only through CI on the exact commit | Gate 1 bullet 6; `docs/PRODUCTION_CUTOVER_CHECKLIST.md` §3 |
| 5 | **Before every staging or production maintenance window**, every release image that may be stale is rebuilt `--pull --no-cache` and re-scanned. On a host with a layer cache `--pull` alone is not enough: if the base digest has not moved, the `apt-get upgrade` layer is a cache hit and yesterday's packages ship again. CI runners start with no layer cache, so `--pull` is sufficient there | runbook §1 (`docs/SAME_HOST_STAGING_MAINTENANCE_WINDOW.md`) |
| 6 | Pre-window image/Trivy evidence is **no older than 24 hours**, and is about the exact image ID the window will run | `scripts/check_image_scan_freshness.py`; `test_image_scan_freshness.py` |
| 7 | The exact CI policy reports **0 fixable CRITICAL/HIGH** — `--ignore-unfixed --severity CRITICAL,HIGH --exit-code 1` | CI per-step policy pin (`test_every_image_scan_keeps_the_whole_policy`) and the pre-window gate, which counts findings itself |
| 8 | **Any new fixable CRITICAL/HIGH makes the window GO/NO-GO RED.** No waiver is created by this policy | the pre-window gate exits 2 |

**The vulnerability policy is not weakened by any of this.** Severity, the
`ignore-unfixed` scope, and the failing exit code are unchanged, and each is
pinned per scan step. A no-fix finding stays governed by
`docs/IMAGE_NOFIX_CVE_ACCEPTANCE.md`; a *fixable* one has no acceptance path
here — the remedy is a rebuild.

## The pre-window gate

Run on the host after the images the window will use are built, and before the
time-budget gate:

```bash
EVID=/var/backups/contraclaim-stg-evidence/<run-id>
mkdir -p "$EVID/trivy"
for IMG in contraclaim-stg-backend:rc1-<sha> contraclaim-stg-client:rc1-<sha> ...; do
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache \
    -v "$EVID/trivy:/out" aquasec/trivy:<pinned> image \
    --ignore-unfixed --severity CRITICAL,HIGH --exit-code 1 --timeout 20m \
    --format json --output "/out/$(echo "$IMG" | tr ':/' '__').trivy.json" "$IMG"
done

python3 scripts/check_image_scan_freshness.py --report-dir "$EVID/trivy" \
  --require "contraclaim-stg-backend:rc1-<sha>=$(docker image inspect -f '{{.Id}}' contraclaim-stg-backend:rc1-<sha>)" \
  --require ...      # one per release image
```

Exit 0 is GO; exit 2 is NO-GO, and is also the answer to anything it cannot
decide: no required image, a missing or duplicated report, a report older than
24 hours or stamped in the future, a report whose `ImageID` is not the image the
tag points at now, a report with no `Results`, or a malformed file.
`--max-age-hours` may tighten the bound and may not loosen it.

**What it cannot see:** the flags a report was produced with. A report generated
with a narrower `--severity` would hide findings. The scan command is therefore
recorded beside the reports in the evidence directory, and the gate is not a
substitute for reading it.

## Known limits

* GitHub runs `schedule` against the **default branch only**. Requirement 1
  takes effect when the workflow reaches `main`; until the release is merged,
  `workflow_dispatch` (or a push to the PR) is how the same run is produced.
* A weekly run is a detection cadence, not a guarantee: an advisory published on
  a Tuesday is found the next Monday, or at the next pre-window rebuild —
  whichever is first. Requirement 5 is what protects a window.
* Third-party images the compose files pin (`mongo`, `redis`, `qdrant`,
  `falkordb`, `clamav`, `httpd`) are not built here and are not in CI's scan set.
  Their scan is recorded as information before a window; bumping them is a
  release change of its own.
