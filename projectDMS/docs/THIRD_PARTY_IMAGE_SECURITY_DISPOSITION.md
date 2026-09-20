# Third-party production images — cutover security disposition

**Status: EXCEPTED UNTIL 2026-10-15, AND ACCEPTED BY THE RELEASE OWNER ON 2026-09-20 — see
"R-A9E cutover exception" and "R-A9G-0 owner acceptance" at the end of this document.** R-A9E (owner decision, 2026-09-16): MongoDB, FalkorDB, Qdrant and httpd are
carried under a dated, bounded exception for the initial production cutover, so they no
longer block it *until that exception expires on 2026-10-15*; remediation runs in the
window 2026-10-06 → 2026-10-15, httpd first. This is not permanent acceptance — on expiry
the cutover blocker below is live again for any image still on these digests. R-A9A
(2026-09-15): the ClamAV row is closed (production runs the validated 1.4.6 since
R-A8Z), and Redis is clean; neither needs an exception.
Re-measured in
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
| ClamAV | `clamav/clamav:1.4` (runs 1.4.5) | `86c2a50372da…` / `b70a05497f80…` | 0 | 3 | 0 | `clamav/clamav:1.4.6` | `71fbb76b397c…` (image `6dc7ff3fabde…`) | **0** (1.5.4 also 0); R-A8Y re-scan 2026-09-14: **0 CRITICAL/HIGH at all** | None found (R-A8Y drill: INSTREAM protocol, healthcheck, persistence, update, privilege) | None (new `clamav_db` volume is seeded from the image) | **ADOPTED ON THE RELEASE BRANCH (R-A8Y); RUNNING IN PRODUCTION (R-A8Z)**: validated in a disposable drill and pinned by digest; certified scan 0 fixable CRITICAL/HIGH. Production's `clamav` service alone was recreated on 1.4.6 under a bounded owner authorization on 2026-09-14 (signature-freshness blocker F-A8X-2 CLOSED: loaded daily 28123, freshclam updating over `egress-net`). Held in production only by the untracked `docker-compose.clamav-r-a8z.yml` override until cutover. This row closes; the other images do not |
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

## R-A8Y: ClamAV 1.4.6 adopted

Driven by F-A8X-2: production's clamd was serving a 70-day-old signature database
(`docs/CLAMAV_SIGNATURE_FRESHNESS.md`). The service definition changed in the same
programme (egress-net, `clamav_db` volume, `FRESHCLAM_CHECKS=12`, a healthcheck
that reloads a stale load, memory 2g -> 3g), so the image was validated together
with it, never on its own.

* **Pinned bytes:** `clamav/clamav:1.4.6@sha256:71fbb76b397cd84a90043caf1178a7f81bd0c131a031e7b0619afd721fbfad41`,
  the index digest R-A8X scanned. **The tag has moved since:** on 2026-09-14
  `clamav/clamav:1.4.6` (and `1.4`) resolved to `f156095071…`, because ClamAV
  rebuilds its tags to refresh the baked database. A tag pin would have adopted
  bytes nobody scanned.
* **Re-scan:** Trivy 0.74.0 with its database refreshed at scan time
  (2026-09-14T03:31Z): 0 CRITICAL, 0 HIGH, fixable or not.
* **Drill:** local disposable Docker, the release compose's own `clamav` service
  (details and results in `docs/CLAMAV_SIGNATURE_FRESHNESS.md` §5).
* **Not changed:** every other third-party image. The disposition above for
  MongoDB, FalkorDB, Qdrant and httpd stands; they blocked cutover until R-A9E, which
  carries them under the dated exception at the end of this document (expiry 2026-10-15).

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
2. **httpd 2.4.68**: adopt once the drill passes. Low risk. (**ClamAV 1.4.6 is
   done** - adopted on the release branch in R-A8Y after its own disposable drill;
   see "R-A8Y: ClamAV 1.4.6 adopted" below.)
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
| ClamAV 1.4.6 (**adopted in R-A8Y**) | Gate 5's live clean/infected scan (P0-005; now Gate 5 bullet 6, never earned) and every smoke - R-A8Y withdrew Gate 9 bullet 4 accordingly |

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

## R-A9E cutover exception — dated, bounded, not permanent acceptance

**Owner decision, 2026-09-16 (phase R-A9E).** MongoDB, FalkorDB, Qdrant and httpd are
**not** upgraded for the initial production cutover: upgrading any of them immediately
before the window would stale a large part of the release evidence listed under
"Evidence made stale by this document" — Gate 6 b2/b3, Gate 8, Gate 9 b3/b4, Gate 2 and
Gate 7 — with no time to re-earn it. The four images below are therefore carried under a
**dated exception that expires**, not accepted permanently.

Every image is pinned by index digest in `docker-compose.prod.yml` /
`docker-compose.mongo-replicaset.yml`, pinned again by
`backend/rbac_backend/tests/test_third_party_image_pins.py`, and was re-measured on
**2026-09-16T05:12Z** with Trivy 0.74.0 against the image id the RUNNING production
container uses (`.claude/context/contract-master` R-A9D evidence,
`60-third-party-image-scan.txt`). "Fixable" counts findings that carry a `FixedVersion`,
read from the JSON, not from the exit code. No tag moved and nothing was pulled.

### E1 — MongoDB

* **IMAGE** `mongo:8.0` (production containers `contraclaim-mongo{1,2,3}`, image id `7281281f68a3`)
* **DIGEST** `sha256:ffa440e8d62533e24a67696ae1bbb46e610ebb3167d65abd122b496ae06d28e6`
* **FIXABLE CRITICAL/HIGH** 1 critical / 272 high = **273** (0 unfixed C/H)
* **EXPOSURE** None in `mongod` itself: the findings are `gosu`, the eight Go-built
  database tools and `js-yaml` in mongosh. Reachable only by an operator running those
  tools on the host; the data tier has no public route.
* **MITIGATION** Internal docker network only, SCRAM-authenticated replica set, digest
  pin, tools run only from operator-initiated backup/restore.
* **WHY UPGRADE DEFERRED** The 8.0.30 candidate has never been validated — the
  compatibility drill is written but unrun — and adopting it stales Gate 6 b2/b3,
  Gate 8 and Gate 9 b3/b4 days before cutover.
* **OWNER ACCEPTANCE** Accepted for the initial production cutover only — owner
  decision R-A9E, 2026-09-16.
* **EXPIRY** 2026-10-15
* **REMEDIATION WINDOW** 2026-10-06 → 2026-10-15

### E2 — FalkorDB

* **IMAGE** `falkordb/falkordb:v4.0.8` (container `contraclaim-falkordb-1`, image id `13ee9b3bfcc1`)
* **DIGEST** `sha256:af5f2aa035390f04fa6d1f0c6353669f5f75c101f6b4f385fcb672a288b4edb8`
* **FIXABLE CRITICAL/HIGH** 11 critical / 137 high = **148** (82 unfixed C/H)
* **EXPOSURE** Debian packages plus the bundled browser UI's `next` / `sharp`. The
  engine is reachable only on the internal data network and is password-protected; the
  browser UI is not published.
* **MITIGATION** Internal network only, `requirepass`, digest pin; credential rotation
  is already scheduled inside the `/FalkorDB` → `/data` cutover.
* **WHY UPGRADE DEFERRED** v4.20.x is roughly ten module releases with a likely one-way
  persistence format, and it would ride the same window as the persistence cutover.
  It stales Gate 2, Gate 8 and Gate 9 b3/b4.
* **OWNER ACCEPTANCE** Accepted for the initial production cutover only — owner
  decision R-A9E, 2026-09-16.
* **EXPIRY** 2026-10-15
* **REMEDIATION WINDOW** 2026-10-06 → 2026-10-15

### E3 — Qdrant

* **IMAGE** `qdrant/qdrant:v1.12.5` (container `contraclaim-qdrant-1`, image id `449e32141460`)
* **DIGEST** `sha256:05fecce7dce45d1254e0468bc037e8210e187fd56fa847688b012293d5f08aae`
* **FIXABLE CRITICAL/HIGH** 6 critical / 78 high = **84** (56 unfixed C/H)
* **EXPOSURE** Debian `perl-base`, `libpcre2`, `libsqlite3`, `gzip` and one Node
  `js-yaml`. The service is internal-only with no public route.
* **MITIGATION** Internal network only, digest pin, storage archived by the nightly
  backup so the pre-upgrade state is recoverable.
* **WHY UPGRADE DEFERRED** No patch remediates: the first remediating line is v1.19.1,
  which means a 1.12 → 1.19 storage-migration chain plus a `qdrant-client` upgrade
  (backend dependency, full suite, image rebuild). The storage migration is
  irreversible, so it cannot ride a cutover window.
* **OWNER ACCEPTANCE** Accepted for the initial production cutover only — owner
  decision R-A9E, 2026-09-16.
* **EXPIRY** 2026-10-15
* **REMEDIATION WINDOW** 2026-10-06 → 2026-10-15

### E4 — Apache httpd (gateway) — highest priority of the four

* **IMAGE** `httpd:2.4` (container `contraclaim-gateway-1`, image id `00fe3afeb8c3`)
* **DIGEST** `sha256:393435ee1a31437adeb1f03c134224c9ce5fa5f527e8c0cb9bea576b0d6fc742`
* **FIXABLE CRITICAL/HIGH** 3 critical / 48 high = **51** (52 unfixed C/H)
* **EXPOSURE** All Debian packages (`perl-base`, `libpcre2`, `libsqlite3`, `gzip`,
  `libssh2`, `openssl`). **This is the only image on the public path**, which is why it
  is remediated first.
* **MITIGATION** The host nginx terminates TLS in front of it, no CGI or perl is in the
  served path, and the image is digest-pinned.
* **WHY UPGRADE DEFERRED** The 2.4.68 candidate is unvalidated, and the gateway is the
  public edge: an upgrade stales Gate 7 and every smoke that runs through it.
* **OWNER ACCEPTANCE** Accepted for the initial production cutover only — owner
  decision R-A9E, 2026-09-16.
* **EXPIRY** 2026-10-15
* **REMEDIATION WINDOW** 2026-10-06 → 2026-10-15

### No exception required

| Image | Digest | Fixable C/H | Why no exception |
|---|---|---:|---|
| ClamAV | `clamav/clamav:1.4.6@sha256:71fbb76b397cd84a90043caf1178a7f81bd0c131a031e7b0619afd721fbfad41` | **0** | Remediated in R-A8Y/R-A8Z after its own disposable drill. Held in production by the untracked `docker-compose.clamav-r-a8z.yml` override, which must be present in EVERY production compose command until it is folded into the tracked compose at cutover. |
| Redis | `redis:7.4-alpine@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99` | **0** | Clean. Keep the current digest. |

### Post-cutover ownership of the exception (added R-A9F, 2026-09-20)

The exception record above carried every field the owner asked for except the standing
owner of the remediation after cutover. That is recorded here for all four images, so it
cannot be lost with the phase that wrote it.

| Field | Value |
|---|---|
| **POST-CUTOVER OWNER** | The release owner (the account that signs Gate 9 bullet 6). Not delegated: the same person who accepts this exception owns closing it. |
| **What the owner owns** | Running the prepared compatibility drill for each candidate on disposable infrastructure inside 2026-10-06 → 2026-10-15; adopting in the order httpd → MongoDB → (FalkorDB, Qdrant decision); re-running Trivy against the adopted digests under the exact CI policy; and re-earning whatever release evidence the adoption stales. |
| **Review trigger before expiry** | On **2026-10-06**, the first day of the remediation window, the owner re-measures the four digests. A count that has grown, or a new CRITICAL with a public-path exposure on `httpd:2.4`, brings the remediation forward rather than waiting for 2026-10-15. |
| **On expiry with no adoption** | The exception lapses automatically on **2026-10-15**. It is not renewed by silence: either a new dated owner decision is written into this document, or the cutover blocker is live again for any image still on these digests. |
| **Where the state is held** | `docker-compose.prod.yml` / `docker-compose.mongo-replicaset.yml` hold the digests; `backend/rbac_backend/tests/test_third_party_image_pins.py` fails if one moves without a documented decision, so a silent upgrade cannot happen either. |

### What this exception does not do

It does **not** accept these findings permanently, and it does not survive its expiry.
On **2026-10-15** the exception lapses: from that date the cutover blocker recorded under
"Owner decisions needed before cutover" is live again for any image still on the digest
above. Inside the remediation window (2026-10-06 → 2026-10-15) the prepared compatibility
drill runs for all four candidates on disposable infrastructure, and adoption order is
**httpd first** (public path, lowest risk), then MongoDB, then a decision on FalkorDB and
Qdrant — which also unblocks the derived-patched-image option that would close the
Debian/Alpine class for all four at once.

## R-A9G-0 owner acceptance — 2026-09-20

**The release owner accepted the dated exception above, for THIS INITIAL CUTOVER ONLY.**
Verbatim record: `docs/R_A9G_OWNER_DECISION_RECORD.md` section 8. This section adds the
owner's acceptance and nothing else — **no image was upgraded, no digest moved, and no
finding count was re-measured in R-A9G-0.**

| Image | Digest (index), as pinned in the production compose files | Fixable C/H | Expiry | Remediation window | Post-cutover owner |
|---|---|---:|---|---|---|
| MongoDB `mongo:8.0` | `sha256:ffa440e8d62533e24a67696ae1bbb46e610ebb3167d65abd122b496ae06d28e6` | **273** | 2026-10-15 | 2026-10-06 → 2026-10-15 | Release Owner |
| FalkorDB `falkordb/falkordb:v4.0.8` | `sha256:af5f2aa035390f04fa6d1f0c6353669f5f75c101f6b4f385fcb672a288b4edb8` | **148** | 2026-10-15 | 2026-10-06 → 2026-10-15 | Release Owner |
| Qdrant `qdrant/qdrant:v1.12.5` | `sha256:05fecce7dce45d1254e0468bc037e8210e187fd56fa847688b012293d5f08aae` | **84** | 2026-10-15 | 2026-10-06 → 2026-10-15 | Release Owner |
| Apache httpd `httpd:2.4` | `sha256:393435ee1a31437adeb1f03c134224c9ce5fa5f527e8c0cb9bea576b0d6fc742` | **51** | 2026-10-15 | 2026-10-06 → 2026-10-15, **first** | Release Owner |

Total **556** fixable CRITICAL/HIGH, as re-measured on 2026-09-16T05:12Z against the image
id each running production container uses. Use the exact digests above and in
`docs/R_A9F_PRODUCTION_MANIFEST.md`; never a floating tag.

**Mandatory review trigger, 2026-10-06.** Re-scan the exact deployed digests on the first
day of the remediation window. **A materially increased finding count, a newly exploitable
CRITICAL, or a new public-path risk may accelerate remediation ahead of the stated window**
rather than waiting for 2026-10-15.

**Not automatically renewable. Silence does not extend it.** On 2026-10-15 the exception
lapses and the cutover blocker is live again for any image still on these digests, unless a
new dated owner decision is written into this document.

**ClamAV 1.4.6 requires no exception. Redis requires no exception.** Confirmed by the owner
on 2026-09-20; both scan 0 fixable CRITICAL/HIGH.
