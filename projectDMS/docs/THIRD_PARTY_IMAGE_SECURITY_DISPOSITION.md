# Third-party production images — cutover security disposition

**Status: OPEN — production cutover BLOCKED on this document.** Re-measured in
release programme R-A8X on 2026-09-13. No third-party image was **upgraded** in
R-A8X, because no candidate's compatibility was proven (see "Why no image was
upgraded"). Every one of them was, however, **digest-pinned to the bytes it already
runs** (see "Digest pins"), because four of the six were floating tags.

Scope: every image the production compose files (`docker-compose.prod.yml` +
`docker-compose.mongo-replicaset.yml`) pull from a registry rather than build.
First-party images (backend, workers, client) are covered separately by
[IMAGE_FRESHNESS_POLICY.md](IMAGE_FRESHNESS_POLICY.md) and CI's image scan.

Policy for cutover (owner, R-A8W): every **fixable** CRITICAL/HIGH finding is
either REMEDIATED or OWNER-ACCEPTED under a narrow, written exception. Nothing is
accepted implicitly, and a supported upgrade that closes a class is preferred to
per-CVE exceptions.

## How the numbers were produced

* Scanner `aquasec/trivy` 0.74.0 (image `sha256:1105aaf5e722…`), vulnerability DB
  refreshed at scan time, 2026-09-13T17:45Z–17:48Z, severities CRITICAL and HIGH,
  **without** `--ignore-unfixed` so unfixed findings are counted too.
* "Fixable" = the finding carries a `FixedVersion`; counted from the JSON, never
  from the scanner's exit code.
* Current images were scanned by the image ID the running production container
  uses (read from `docker inspect`). Candidates were pulled **by index digest
  only**, so no production tag moved: the six production tags resolved to the
  same image IDs before and after the scan.
* Evidence: `/var/backups/contraclaim-stg-evidence/R-A8X-IMAGES-20260913T174531Z`
  (`01-scan-log.txt`, `02-matrix.txt`, `trivy/*.json`, sealed by `SHA256SUMS`).
  Production health was 13 running / 0 restarts / ready 200 before and after.

R-A8W's figures (273 / 148 / 84 / 51 / 3 / 0 = 559) reproduce exactly on the
current DB.

## Matrix

| Image | Current pin | Current digest (index) / image ID | Fixable C | Fixable H | Unfixed C/H | Latest compatible candidate | Candidate digest (index) | Candidate fixable C/H | Breaking change? | Migration? | Disposition |
|---|---|---|---:|---:|---:|---|---|---:|---|---|---|
| MongoDB | `mongo:8.0` (runs 8.0.26) | `ffa440e8d625…` / `7281281f68a3…` | 1 | 272 | 0 | `mongo:8.0.30` (same 8.0 series) | `4a0f30875898…` (amd64 `73459aa7c26c…`) | 1 / 96 = **97** | Not expected (patch release); **unproven** | None expected; **unproven** | CANDIDATE, NOT VALIDATED. Residual 97 needs an owner decision |
| FalkorDB | `falkordb/falkordb:v4.0.8` (module 40008) | `af5f2aa03539…` / `13ee9b3bfcc1…` | 11 | 137 | 82 | `v4.20.4` (Debian 13) | `adbddd418916…` | 2 / 9 = **11** (alpine variant: 12) | **Possible**: 20 minor releases of the graph module | **Likely one-way**: persistence written by v4.20 may not load in v4.0.8, so rollback needs the pre-upgrade archive | CANDIDATE, NOT VALIDATED. Owner decision needed |
| Qdrant | `qdrant/qdrant:v1.12.5` | `05fecce7dce4…` / `449e32141460…` | 6 | 78 | 56 | `v1.12.6` closes **nothing** (84). The only remediating line is `v1.19.1` | `12364fe851b9…` | 3 / 10 = **13** | **Yes**: seven minor versions; backend pins `qdrant-client==1.12.2` | **Yes**: storage upgrade chain 1.12→1.13→…→1.19, one minor at a time | OWNER DECISION: no safe patch exists |
| Apache httpd (gateway) | `httpd:2.4` | `393435ee1a31…` / `00fe3afeb8c3…` | 3 | 48 | 52 | `httpd:2.4.68` (Debian 13) | `979c38c2228d…` | 3 / 13 = **16** (alpine: 0 / 22) | Not expected (same 2.4 line, same Debian major); **unproven** | None | CANDIDATE, NOT VALIDATED |
| ClamAV | `clamav/clamav:1.4` (runs 1.4.5) | `86c2a50372da…` / `b70a05497f80…` | 0 | 3 | 0 | `clamav/clamav:1.4.6` | `71fbb76b397c…` | **0** (1.5.4 also 0) | Not expected (patch) ; **unproven** | None | CANDIDATE, NOT VALIDATED. Lowest-risk remediation |
| Redis | `redis:7.4-alpine` | `6ab0b6e73817…` / `487efc061638…` | 0 | 0 | 0 | current is clean (`7.4.11-alpine` also 0) | — | 0 | — | — | REMAINS. Digest-pin the running image when pins next change |
| **Total** | | | **21** | **538** | | | | **137** if every candidate incl. Qdrant 1.19.1 is adopted | | | |

## What the residual after the candidates consists of

Measured per package from the candidate JSON:

* **MongoDB 8.0.30 — 97.** None is in `mongod`. `gosu` (Go stdlib 1.24.6: 1 C,
  21 H); the eight Go-built database tools — `mongodump`, `mongorestore`,
  `bsondump`, `mongoexport`, `mongoimport`, `mongofiles`, `mongostat`,
  `mongotop` — each with Go stdlib 1.26.5 (8 H) and `golang.org/x/crypto`
  0.54.0 (1 H); and `js-yaml` 3.13.1 in mongosh (3 H). An image upgrade cannot
  close these until MongoDB rebuilds the tools; the backup and restore scripts
  run `mongodump`/`mongorestore` inside this image.
* **FalkorDB v4.20.4 — 11.** Debian packages with fixes already published
  (`libpcre2-8-0`, `libsqlite3-0`, `gzip`, `openssl`/`libssl3t64`), plus the
  bundled browser UI's Node packages `next` 16.2.12 (2 C) and `sharp` 0.35.3.
* **Qdrant v1.19.1 — 13.** Debian `perl-base` (3 C, 4 H), `libpcre2-8-0`,
  `libsqlite3-0`, `gzip`, and one Node `js-yaml`.
* **httpd 2.4.68 — 16.** All Debian: `perl-base` (3 C, 4 H), `libpcre2-8-0`,
  `libsqlite3-0`, `gzip`, `libssh2-1t64`, `openssl`.

Every Debian and Alpine package above has a fixed version in its distribution
already, so the class is closable by a thin derived image (`FROM <digest>` plus a
distribution upgrade), the same remedy `IMAGE_FRESHNESS_POLICY.md` applies to the
first-party base images. That would leave MongoDB's Go tooling (97) and
FalkorDB's browser-UI Node packages (3) as the only residual needing acceptance.
It is a supply-chain change (third-party images become first-party builds that CI
must scan), so it is recorded as the recommendation, not done.

## Digest pins

The independent R-A8X review found that "no pin moved" did not mean "no image
moved": `mongo:8.0`, `httpd:2.4`, `clamav/clamav:1.4` and `redis:7.4-alpine` are
floating tags, so any `compose pull` (`scripts/deploy.sh` runs one), pruned cache
or new host would fetch whatever they resolve to today — the unvalidated 8.0.30,
2.4.68 and 1.4.6 — which is implicit adoption.

All six images in `docker-compose.prod.yml` and `docker-compose.mongo-replicaset.yml`
are now `tag@sha256:<index digest>`, using the index digests the running production
containers report (`RepoDigests`), each confirmed to still resolve on the registry
(HTTP 200). The running bytes, and the bytes R-A8W staging ran, are unchanged, so no
evidence is staled by the pin. `backend/rbac_backend/tests/test_third_party_image_pins.py`
refuses a floating third-party reference and pins each digest, so moving one is a
deliberate change to that test.

Not covered by the compose pins (recorded, not changed in R-A8X):
* `busybox` (untagged, therefore `latest`) runs as root with every production volume
  mounted in `scripts/backup_volume.sh` and `scripts/production_restore_volumes.sh`;
* the host nginx TLS terminator is a distribution package, outside this matrix.

## Why no image was upgraded in R-A8X

The owner's rule is "if safe upgrades are proven, update the pins". A disposable
compatibility drill was written for all five candidates
(`R-A8X` receipt, "compatibility drill") — replica-set startup, transactions
through the application's own driver, SCRAM, restore parity and rollback dump for
MongoDB; the production rescue archive loaded into both FalkorDB engines with
identical read and write query results, persistence in `/data`, the backup
contract and a rollback read; the full Qdrant chain on a copy of production
storage with search results compared at every step through the backend's own
client; `httpd -t`, module lists and proxied responses with security headers for
the gateway; and the application's own INSTREAM scan of a clean file and EICAR
for ClamAV.

**It did not run.** Launching it on the production host (disposable,
`--internal` networks, run-owned names, archives read-only) was refused by the
session's permission policy, and that refusal was not worked around. So every
candidate above is "NOT VALIDATED", and pinning an unproven data-tier image would
be exactly the implicit acceptance the policy forbids.

## Owner decisions needed before cutover

1. **Run the compatibility drill** (authorise it on the production host under the
   standing disposable-drill authorisation, or supply a separate drill host). It
   is ready to run and takes roughly 20–30 minutes.
2. **ClamAV 1.4.6 and httpd 2.4.68**: adopt once the drill passes. Low risk.
3. **MongoDB 8.0.30**: adopt once the drill passes, and decide the residual 97
   (Go tooling and mongosh, not the server): accept narrowly with a revisit date,
   or wait for an upstream tools rebuild.
4. **FalkorDB v4.20.4**: a 20-release jump with a likely one-way persistence
   format. Decide whether it rides the same cutover as the `/FalkorDB` → `/data`
   move (more change in one window) or its own window, after the drill proves
   read/write parity on the production graph.
5. **Qdrant**: no patch remediates. Either authorise the 1.12→1.19 chain plus a
   `qdrant-client` upgrade as its own change (backend dependency, rebuild, full
   suite, chain drill on production storage), or accept 84 findings narrowly for
   the cutover with a dated follow-up.
6. **Derived patched images** for the Debian/Alpine residual (recommended).

## Evidence made stale by this document

None on dependency grounds: the digest pins name the image IDs production and the
R-A8W staging run already used, so Gate 6 bullet 3 and Gate 9 bullet 4 stand.

Adopting a candidate later stales, at minimum:

| Candidate adopted | Evidence it stales |
|---|---|
| MongoDB 8.0.30 | Gate 6 b2 and b3 (fresh install and production-copy upgrade), Gate 8 restore drill, Gate 9 b3 and b4 smokes |
| FalkorDB v4.20.x | Gate 2 graph round trip, Gate 8 FalkorDB backup/recovery, Gate 9 b3 and b4 |
| Qdrant v1.19.x (+ client) | Gate 2 vector round trip, Gate 8 Qdrant backup, Gate 9 b3 and b4, the full backend suite and image scan (client dependency) |
| httpd 2.4.68 | Gate 7 edge/deployment evidence and every smoke, which runs through the gateway |
| ClamAV 1.4.6 | Gate 5's live clean/infected scan (P0-005) and every smoke |

Separately, the R-A8X source changes to `production_backup.sh` / `backup_volume.sh`
touch the backup path that Gate 8 measured; the next window's canonical backup on a
fresh install exercises them.

**Rollback is one-way for two candidates.** Qdrant does not support downgrade: its
storage migration is irreversible, so rolling back after 1.12→1.19 means restoring
the pre-upgrade storage archive, not starting the old image. FalkorDB persistence
written by a newer module is expected to be the same. The cutover's pre-change
archives are therefore the rollback path, not a formality.

**Option not yet listed above:** `falkordb/falkordb-server` ships the same database
without the browser UI, which would remove the bundled `next`/`sharp` findings. The
FalkorDB jump is about ten minor releases (the line uses even-numbered minors), not
twenty.
