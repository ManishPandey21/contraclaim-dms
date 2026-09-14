# ClamAV signature freshness — diagnosis, release fix, and gate

**Release programme R-A8Y, 2026-09-14.** Production was read, never changed.
The fix lives on `release/contraclaim-rc1` only and reaches production at cutover.

## 1. What production was doing (read-only diagnosis)

Measured on the production host at 2026-09-14T02:45Z with `docker inspect`,
`docker exec` read commands, `docker logs` and non-mutating connectivity probes.
freshclam was not run, nothing was restarted, nothing was written.

| # | Fact | Measured value |
|---|---|---|
| 1 | Image | `clamav/clamav:1.4` → image `sha256:b70a05497f80…`, index digest `sha256:86c2a50372da…` (image built 2026-07-06T01:24Z) |
| 2 | Container | `contraclaim-clamav-1`, created 2026-07-09T07:03:16Z, last started 2026-09-13T16:18:42Z, RestartCount 0, healthy |
| 3 | Engine | ClamAV 1.4.5 (clamd `VERSION`, clamscan and clamdscan agree) |
| 4 | Loaded database | `ClamAV 1.4.5/28051/Sun Jul  5 06:24:23 2026` — daily **28051**, built **2026-07-05 06:24:23 UTC** |
| 5 | Files | `daily.cvd` 23,414,142 B mtime 2026-07-06 01:24:30 (sigtool: v28051, build 05 Jul 2026 06:24, verification OK); `main.cvd` 89,072,577 B mtime 2026-07-06 01:24:37 (v63, build 16 Dec 2025); `bytecode.cvd` 281,702 B mtime 2026-07-06 01:24:44 (v339, build 11 Sep 2025); `freshclam.dat` 90 B mtime 2026-07-09 07:03:18 (container creation). No `.cld` files. Every CVD mtime is the image build, not an update |
| 6 | freshclam running? | **Yes**: PID 17 `freshclam --checks=1 --daemon --foreground --stdout --user=clamav` (image `/init` default); clamd PID 19 |
| 8/9 | Logs since creation | 11,230 lines retained from 2026-07-09 (nothing rotated away). 68 update runs, **68 `ERROR: Update failed.`**, 0 `updated`, 0 `up-to-date`. Per run: `WARNING: Can't query current.cvd.clamav.net` → `Invalid DNS reply. Falling back to HTTP mode.` → `remote_cvdhead: Download failed (6) … Could not resolve hostname` ×3 → `Giving up on https://database.clamav.net...`. clamd: `The virus database is older than 7 days!` on every start |
| 11 | Networks | `contraclaim_service-net` only |
| 12 | service-net internal? | **`internal=true`** (also `data-net`; `egress-net` and `edge-net` are not internal) |
| 13 | Route | `ip route`: only `172.19.0.0/16 dev eth0`; **no default route**; `ip route get 1.1.1.1` → `Network unreachable` |
| 10 | DNS | `/etc/resolv.conf` → Docker embedded resolver 127.0.0.11; `getent hosts database.clamav.net` rc=2 after 10 s; `nslookup` → **SERVFAIL** |
| — | TCP / HTTPS | host-resolved `104.17.196.15`: `nc -z` :443 and :80 rc=1; `wget --spider https://104.17.196.15/` → `can't connect to remote host: Network unreachable`. By name: `nc: bad address`. The **host** reaches the mirror (HTTPS answered), and the backend container, which is on `egress-net`, resolved the name in 0.04 s and connected to :443 |
| 14 | `/var/lib/clamav` persisted? | **No.** `Mounts: []`, the image declares no VOLUME; the database lives in the container's writable layer |
| — | Permissions | `/var/lib/clamav` `clamav:clamav` mode 2755, writable by uid `clamav` (`test -w`, nothing written) |
| — | Memory | 946.5 MiB of the 2 GiB limit, `ConcurrentDatabaseReload` at its default (on) |

**Signature age at 2026-09-14T02:45:02Z**

| Basis | Timestamp | Age |
|---|---|---|
| Loaded database (clamd `VERSION`) — the criterion | 2026-07-05 06:24:23 UTC | **1,700.34 h = 70.85 days** |
| `daily.cvd` header build time (sigtool) | 2026-07-05 06:24 UTC | 1,700.3 h = 70.85 days |
| `daily.cvd` file mtime (image layer) | 2026-07-06 01:24:30 UTC | 1,681.3 h = 70.05 days |

### Root cause

Classification: **B (freshclam running but no egress)** and **C (DNS failure)**, both
from one mechanism, plus **F (no persistence)**. Not A (freshclam is running), not D
(no request ever left the container, so the mirror never had a chance to refuse), not
E (the database directory is owned by and writable for `clamav`).

> clamav is attached only to `service-net`, which is `internal: true`, so Docker gives
> the container no default route and its embedded resolver refuses external names;
> freshclam therefore failed every check since 2026-07-09 and clamd kept serving the
> daily database baked into the image on 2026-07-05 — and because nothing mounts
> `/var/lib/clamav`, even a successful update would be lost on the next recreation.

Why nothing noticed: the container healthcheck (`clamdscan --ping`), the backend's
`/health/ready` dependency probe (`nPING` → `PONG`) and every Gate 5 bullet ask whether
clamd *answers*. A stale database still answers, and still detects EICAR.

## 2. Release fix

| Change | Where | Why |
|---|---|---|
| `clamav` joins `egress-net` (plus `service-net`) | `docker-compose.prod.yml` | gives freshclam a route and resolvable DNS; `service-net` stays internal |
| `clamav_db:/var/lib/clamav`, dedicated named volume | `docker-compose.prod.yml` | signatures survive container recreation; seeded by Docker from the image's own database the first time |
| `FRESHCLAM_CHECKS: "12"` | `docker-compose.prod.yml` | the image default of 1 check a day can leave the database ~48 h old — the FAIL threshold — after a single missed check |
| healthcheck: reload a stale load, once | `docker-compose.prod.yml` | found by the drill (§5): the image's `/init` starts freshclam and clamd together; freshclam can finish before clamd listens (`Clamd was NOT notified`), clamd keeps the database it started loading and its SelfCheck reports `Database status OK`. The healthcheck compares the daily version in the CVD/CLD header with the loaded one and requests one RELOAD; unreadable VERSION, or a newer database still not loaded 10 min later, is **unhealthy** |
| memory limit 2g → 3g | `docker-compose.prod.yml` | concurrent reload holds two engines: drill peak 1,952–1,969 MiB, 96 MiB under the old limit, and the reload now runs daily |
| image `clamav/clamav:1.4.6@sha256:71fbb76b…` | `docker-compose.prod.yml`, `test_third_party_image_pins.py` | pinned after the drill passed (§5) |
| `scripts/check_clamav_signature_freshness.py` | new | the gate (§3) |
| `scripts/lib/clamav_readiness.sh` + call in `post_deploy_verify.sh` | new / changed | runs the gate from the backend container on every deploy |

Not changed, deliberately: `/health/live` (no dependency at all), `/health/ready`
(ClamAV stays a *degraded* dependency — a mirror outage must never take the application
out of rotation), backend source, the backup set (the signature cache is regenerable and
~110 MB of daily churn; restoring it would put old signatures back).

### Network security review

Mechanically, from the release compose (`test_clamav_update_path.py`):

* **No new peer.** `egress-net` members are backend, contract-worker, document-worker and
  document-worker-canary; every one of them is already on `service-net` with clamav. The
  guard fails if any service that is not on `service-net` ever joins `egress-net`.
* **No data tier.** clamav is not on `data-net` (Mongo, Qdrant, FalkorDB); `data-net` and
  `service-net` stay `internal: true`.
* **No host exposure.** No `ports`, no `network_mode`, no `pid`/`ipc`/`userns_mode`, not
  `privileged`, no `cap_add`/`devices`, no bind mount, no Docker socket; the only mount is
  `clamav_db`, which no other service mounts.

**The minimum new attack surface is outbound connectivity from clamd's container**:
NAT'd egress to any internet destination and external DNS through Docker's resolver.
clamd parses hostile uploads, so a compromised clamd could now exfiltrate what it scans
or fetch a second stage; before, it could only talk to service-net peers it already
reached. Residual mitigations: clamd and freshclam run as `clamav` (init as root, as
before); no setuid/setgid binary in either image; memory capped at 3 g; no credentials
in the container. An egress allow-list to
`database.clamav.net` would need a forward proxy or host firewall rules outside compose
and is recorded as an owner option, not done.

## 3. The freshness gate

`scripts/check_clamav_signature_freshness.py`, stdlib only.

* **Source of truth:** clamd's `VERSION` reply — the database the daemon has *loaded*.
  File mtimes are never used (a `daily.cld` written but not reloaded would look fresh).
* **Thresholds:** WARN when age > `CLAMAV_SIGNATURE_WARN_HOURS` (default 24); FAIL when
  age > `CLAMAV_SIGNATURE_MAX_AGE_HOURS` (default 48). Both configurable by environment
  or flag; `warn > max` or a non-positive value is refused.
* **Fails closed on:** clamd unreachable; reply unparsable; reply with no database part;
  timestamp more than 1 h in the future (clock/timezone fault); age > maximum; clean file
  not answered `OK`; EICAR not answered `FOUND`; a required check that produced no
  result; a non-finite or incoherent threshold.
* **Partial runs cannot pose as readiness:** only the full run prints
  `CLAMAV_READINESS=`; `--no-scan` / `--version-text` print `CLAMAV_FRESHNESS=`.
  `--now` needs `--allow-simulated-clock` and an explicit UTC offset.
* **Exit codes:** 0 OK, 3 WARN, 1 FAIL, 2 invalid policy/invocation. Callers treat
  everything except 0 and 3 as failure.
* EICAR is assembled at runtime; no file in the repository carries the string.

**Integration.** `post_deploy_verify.sh` pipes the checker into
`docker compose exec -T backend python -`, so it runs on the network path uploads take
and the version that runs is the deployed checkout's. Exit 3 is a WARN, anything else a
counted FAIL that does not abort the remaining checks. `ANTIVIRUS_ENABLED=false` on the
running backend, or no backend container, is a FAIL.

**Gate 5.** Bullet 6 (added unticked) requires, from one canonical deployed-stack run:
clamd reachable, loaded signatures within the maximum age, clean file accepted, EICAR
rejected.

**Why a mirror outage is not an application outage.** Liveness has no dependency;
readiness reports ClamAV as degraded only when clamd does not answer; uploads fail closed
only when clamd cannot scan. A mirror outage stops updates, the loaded database keeps
scanning, and the gate stays green until the database exceeds the maximum age — then the
*release verification* goes red, not the application.

## 4. Operations

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
  exec -T backend python - < scripts/check_clamav_signature_freshness.py
```

A FAIL on age: read `docker logs <clamav>` for `Update failed` / `Could not resolve`,
confirm the container is on `egress-net`, confirm the `clamav_db` mount. Do not relax the
maximum to pass a release.

## 5. Disposable drill and candidate image

Local Docker Desktop (engine 29.7.2, 4 GB VM), 2026-09-14, never the production host.
The drill starts the **release compose's own `clamav` service** (project `ra8y-drill`,
placeholder env), so networks, volume, healthcheck and limits are the shipped ones;
probes run from a Python container on `service-net`, where the backend sits. Three runs:
run 1 found the startup race and was stopped; run 2 validated on the 2g limit and found
the memory margin; **run 3 on the final compose (1.4.6 digest, 3g) is the run of record.**
A fourth, targeted run exercised the hardened healthcheck (below).

| # | Requirement | Run 3 result |
|---|---|---|
| 1 | new `clamav_db` volume starts safely | PASS, healthy in 32 s (Docker seeds the volume from the image: daily 28115, built 2026-09-06) |
| 1b | truly empty volume (`volume-nocopy`) | PASS: `/init` downloaded main/daily/bytecode, clamd healthy, daily 28122 |
| 2 | FreshClam obtains current signatures | PASS, daily 28115 → 28122 |
| 2c | startup race | **REPRODUCED** (`Clamd was NOT notified`), healed by the healthcheck reload: `Database correctly reloaded (3628062 signatures)` |
| 2b/2d | reload and overlapping reloads under the limit | PASS, peak 1,969 MiB of 3,072, no OOM, no restart |
| 3 | loaded database within policy | PASS, `CLAMAV_READINESS=OK`, age 21.0 h |
| 4 | clean file | PASS: checker `clean_file_accepted`, and the byte-identical `services/antivirus_service.py` `scan_file` → `(True, None)` |
| 5 | EICAR | PASS: checker `eicar_rejected`, `AntivirusService` → `(False, 'Eicar-Signature')` — backend INSTREAM protocol compatible |
| — | healthcheck | PASS (healthy) |
| — | `compose exec -T` forwards stdin (the post_deploy mechanism) | PASS, 12,869 bytes |
| — | privilege | PASS: not privileged, no cap_add, only `clamav_db` mounted, freshclam/clamd as `clamav`, setuid/setgid set identical to 1.4.5 (empty) |
| 6 | recreate | PASS, new container id |
| 7 | same DB survives | PASS, sha256 of every CVD/CLD identical |
| 8 | no regression | PASS, 28122 → 28122 |
| 9 | FreshClam after restart | PASS, `daily.cld database is up-to-date`, no ERROR |
| 10 | **RED** remove egress-net (compose override) | RED as required: no route, `nslookup` fails, `Could not resolve hostname` / `Update failed` |
| 10 | no egress, valid DB | PASS: clamd still scans, gate green |
| 11a | **RED** age > max (`--now` +49 h) | RED, exit 1 |
| 11b | **RED** genuine stale bytes: production's 1.4.5 image and its July database | RED, exit 1 |
| 12 | **RED** remove the volume (compose override), recreate | RED as required: 28122 → 28115 (back to the baked database) |
| 13 | candidate scan (Trivy 0.74.0, DB refreshed 03:31Z) | 0 CRITICAL, 0 HIGH |
| 14 | **RED** healthcheck without the reload (upstream default) | RED as required: race occurred, loaded 28115 while disk held 28122 after 120 s |

Healthcheck drill (the final, hardened healthcheck; project `ra8y-hc`, final compose):

| # | Requirement | Result |
|---|---|---|
| A | race heal on a new volume, marker cleared | PASS: race occurred (1), loaded == disk, marker absent, healthy |
| B | unreadable VERSION (`nc` removed) is visible | PASS: `unhealthy`, last probe `clamd VERSION unreadable:`; healthy again once restored |
| C | a newer daily that will not load | PASS: **1** reload attempt in 12 min (not ~48); clamd logged `Database reload failed, keeping the previous instance` and kept scanning (PONG throughout); `unhealthy` at +742 s with probe `daily 99999 on disk but 28122 loaded 10 min after RELOAD` |

Permanent controls (no Docker needed, run in CI):
`test_clamav_update_path.py` (egress, persistence, sharing, internal networks, new
peers, privilege, namespaces, ports, Docker socket, cadence, memory, self-heal, reload
storm, unreadable VERSION — each with a mutation), `test_clamav_signature_freshness.py`
(stale, unparsable, no timestamp, unreachable, EICAR not detected, clean refused, partial
runs, simulated clock), `test_post_deploy_clamav_readiness.py` (exit-code mapping through
the real library under `set -euo pipefail`, the backend-container invocation, the
disabled-antivirus parser).

## 6. Evidence this changes

| Evidence | Disposition | Why |
|---|---|---|
| Gate 5 bullets 1–5 | kept | configuration and unit evidence; nothing they measure changed |
| Gate 5 live antivirus (P0-005) | never earned; now Gate 5 bullet 6, unticked | requires a deployed-stack run |
| Gate 9 bullet 4 (smoke after restore) | **withdrawn, stale** | the ClamAV image, network, volume and healthcheck changed, which the image disposition pre-registered as staling every smoke; and the smoke now carries a mandatory antivirus check that run never made |
| Gate 9 bullet 3 | already unticked (R-A8X) | re-earned by the next canonical run, which will now include ClamAV |
| Gate 7 bullet 3 (compose renders) | kept | no new interpolated variable (0 `${` added); the full prod + replica-set file set renders exit 0, 14 services, no unset-variable warning with placeholder values |
| Gate 7 bullets 1, 2, 4–7 | kept | none measures ClamAV; `/health/live` and `/health/ready` code unchanged |
| Gate 2, Gate 3 bullet 1 | kept | AI/vector/graph/Redis round trips and session E2E do not touch ClamAV |
| Gate 6, Gate 8 | kept | no migration, seed or restore path changed; `clamav_db` is deliberately outside the backup set |

Readiness: 81 → **78** (raw 78.17): Gate 5 5/5 → 5/6, Gate 9 1/6 → 0/6.

**Cutover notes.** On adoption, compose recreates clamav with the new network and an
empty `clamav_db` (seeded from the 1.4.6 image, database built 2026-09-06, older than
48 h by then): expect the race and its heal, then `post_deploy_verify.sh` must show
`CLAMAV_READINESS=OK` before the window closes. Production host memory: 22 GB total,
≈17 GB available when read; the extra 1 GB limit is headroom used for ~25 s per update.
