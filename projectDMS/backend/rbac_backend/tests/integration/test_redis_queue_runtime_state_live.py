"""Gate 2 bullet 5: Redis queue and runtime-state paths, against a real engine.

R-A6 recorded that bullet 5 was the one Gate 2 criterion with **no
evidence-eligible artefact anywhere in the tree**. Bullets 1-4 each map onto a
`RUN_EXTERNAL_INTEGRATION_TESTS`-gated module; bullet 5 mapped onto a pair of
hand-typed `redis-cli` round trips recorded in a document. The gate's own
evidence convention requires `evidence: <path>` naming a repository path that
exists, so bullet 5 could never be ticked and Gate 2 capped at 5/6. This module
is that path.

**It tests the production paths, not Redis.** A `PING` proves the container is
up and proves nothing about the application. What Gate 2 claims is that *the
queue and runtime-state code this release ships* works against the deployed
engine, so every assertion here goes through a production class:

* reachability and authentication - `RuntimeStateService.get_redis()`, which is
  what sessions, the cache and every rate limiter call. It returns `None` and
  logs a warning when Redis is unreachable or rejects the password, so an
  unauthenticated or unreachable engine surfaces here as a failure rather than
  as a fallback nobody notices;
* runtime state and TTL - `InMemoryCache`, the runtime-state consumer, whose
  Redis branch is `SETEX`/`GET`/`DEL` under the `cache:` prefix. Expiry is
  proven by an actual expiry, not by reading back the TTL we just set;
* queue enqueue and dequeue - `ContractIngestQueue.enqueue()` and the exact
  `BRPOPLPUSH` the worker loop issues, plus `_push_unique`'s idempotency and
  `_recover_stale_processing_jobs_once`'s visibility-timeout recovery. Those
  four are the whole queue contract in production.

**Namespace and cleanup.** Every key this module creates carries a
`stg_gate2_<run token>` segment, so two concurrent runs cannot collide and
residue is attributable. The last test asserts that nothing under the prefix
survived in either database - a cleanup that swallows its own failure is how
the FalkorDB instance accumulated an orphaned graph.

**Negative controls ship with it.** A live suite that cannot fail is not
evidence. `test_wrong_port_is_refused_live` and
`test_wrong_password_is_refused_live` prove that the reachability assertion
above is load-bearing: point the same production class at the wrong port or
give it the wrong password and it must not report a usable client.

Run against staging:

    CONTRACLAIM_STAGING_GATE=1 ENVIRONMENT=staging \\
      RUN_EXTERNAL_INTEGRATION_TESTS=1 \\
      REDIS_TEST_URL=redis://:<password>@<staging-host>:6379/0 \\
      pytest backend/rbac_backend/tests/integration/test_redis_queue_runtime_state_live.py

Run against a disposable local Redis (developer mode, not gate evidence):

    RUN_EXTERNAL_INTEGRATION_TESTS=1 REDIS_TEST_URL=redis://127.0.0.1:6379/0 \\
      pytest backend/rbac_backend/tests/integration/test_redis_queue_runtime_state_live.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import pytest

from rbac_backend.core.config import settings
from rbac_backend.services.cache_service import InMemoryCache
from rbac_backend.services.contract_ingest_queue import ContractIngestQueue
from rbac_backend.services.runtime_state import RuntimeStateService
from rbac_backend.tests import staging_gate

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_external_service,
]

#: Endpoint variable. Named like its Falkor and Qdrant siblings
#: (`FALKOR_TEST_HOST`, `QDRANT_TEST_URL`) and deliberately given no default:
#: the defaults on those two are exactly the hazard R-A6 recorded.
REDIS_TEST_URL_ENV = "REDIS_TEST_URL"

#: Production splits Redis by database: db 0 carries the contract ingest queue,
#: db 1 carries runtime state, sessions and the drafting/filing queues. See the
#: `CONTRACT_QUEUE_REDIS_URL` and `RUNTIME_STATE_REDIS_URL` values in
#: `docker-compose.prod.yml`. The staging run must exercise the same split, or
#: it proves nothing about a deployment that uses two databases.
QUEUE_DB = 0
RUNTIME_STATE_DB = 1

#: One token per process, so every key is attributable to this run.
RUN_TOKEN = uuid.uuid4().hex[:10]
KEY_NAMESPACE = f"stg_gate2_{RUN_TOKEN}"

#: Every pattern this module can create a key under, in either database.
RUN_KEY_PATTERNS = (
    f"{KEY_NAMESPACE}*",
    f"cache:{KEY_NAMESPACE}*",
    f"contract_ingest_job:{KEY_NAMESPACE}*",
)


class _PinnedRuntimeState(RuntimeStateService):
    """The production adapter with its URL supplied rather than read from settings.

    Subclassed rather than monkeypatched onto the class: patching
    `RuntimeStateService.redis_url` would change the adapter for every other
    test in the process, including ones that legitimately expect it to be None.
    """

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url

    @property
    def redis_url(self) -> Optional[str]:
        return self._url


def _require_live_redis() -> str:
    """The staging endpoint, or a skip - and under the staging gate, a failure."""
    staging_gate.require_live_environment(
        REDIS_TEST_URL_ENV,
        skip=pytest.skip,
        fail=pytest.fail,
    )
    # Resolved through the shared seam, so staging mode refuses a localhost
    # endpoint here for the same reason it refuses one for Falkor and Qdrant.
    return staging_gate.resolve_url(REDIS_TEST_URL_ENV, "")


def _url_with_db(url: str, db: int) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{db}", "", parsed.fragment))


def _url_with_netloc(url: str, netloc: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, netloc, "/0", "", ""))


def _queue_key(name: str) -> str:
    return f"{KEY_NAMESPACE}_{name}"


@asynccontextmanager
async def _runtime_state(url: str) -> AsyncIterator[RuntimeStateService]:
    """A runtime-state service bound to THIS test's event loop.

    Async fixtures are unsupported in this repository (`backend/conftest.py`
    runs each coroutine test in its own `asyncio.run` loop), and the production
    singleton caches a client on the loop that created it. A per-test instance
    is the only shape that does not fail in-suite while passing alone.
    """
    service = _PinnedRuntimeState(url)
    try:
        yield service
    finally:
        await service.close()


@asynccontextmanager
async def _queue(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Tuple[ContractIngestQueue, str, str]]:
    """The production queue, pointed at run-owned list names."""
    ready = _queue_key("contract_ingest_queue")
    processing = _queue_key("contract_ingest_processing")
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_REDIS_URL", url, raising=False)
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_NAME", ready, raising=False)
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_PROCESSING_NAME", processing, raising=False)

    queue = ContractIngestQueue()
    try:
        yield queue, ready, processing
    finally:
        # A cleanup failure must not REPLACE the test's own failure on the way
        # out - but it must not vanish either. "Silent failures are the house
        # pattern here" (CLAUDE.md): deferring to the residue test alone loses
        # the signal entirely under `-k`, `--deselect` or an early abort.
        client = queue._redis
        cleanup_error: Optional[BaseException] = None
        if client is not None:
            try:
                await _delete_run_keys(client)
            except Exception as exc:  # noqa: BLE001 - re-raised below when safe
                cleanup_error = exc
        await queue.close()
        if cleanup_error is not None and sys.exc_info()[0] is None:
            raise AssertionError(
                f"Gate 2 Redis run {RUN_TOKEN} could not delete its own keys from "
                f"a shared engine: {cleanup_error!r}. Delete anything matching "
                f"{KEY_NAMESPACE}* manually."
            ) from cleanup_error


async def _scan_run_keys(client: Any) -> List[str]:
    found: List[str] = []
    for pattern in RUN_KEY_PATTERNS:
        async for key in client.scan_iter(match=pattern, count=500):
            found.append(key.decode() if isinstance(key, bytes) else str(key))
    return sorted(set(found))


async def _delete_run_keys(client: Any) -> None:
    keys = await _scan_run_keys(client)
    if keys:
        await client.delete(*keys)


# --------------------------------------------------------------------------- #
# 1. Reachability and authentication, through the production adapter
# --------------------------------------------------------------------------- #


async def test_runtime_state_adapter_reaches_the_engine_live() -> None:
    url = _url_with_db(_require_live_redis(), RUNTIME_STATE_DB)
    async with _runtime_state(url) as service:
        client = await service.get_redis()
        assert client is not None, (
            f"RuntimeStateService could not reach or authenticate against "
            f"{REDIS_TEST_URL_ENV} db {RUNTIME_STATE_DB}. It returns None and falls "
            "back to local state on connection or auth failure, so this is the "
            "point at which an unreachable or password-rejecting engine becomes "
            "visible rather than silently degrading sessions and rate limits."
        )
        assert await client.ping() is True


# --------------------------------------------------------------------------- #
# 2. Runtime state: write, read, delete, and real expiry
# --------------------------------------------------------------------------- #


async def test_runtime_state_write_read_delete_live(monkeypatch: pytest.MonkeyPatch) -> None:
    url = _url_with_db(_require_live_redis(), RUNTIME_STATE_DB)
    async with _runtime_state(url) as service:
        client = await service.get_redis()
        assert client is not None
        cache = InMemoryCache()
        monkeypatch.setattr(cache, "_runtime_state", service, raising=True)

        key = f"{KEY_NAMESPACE}_runtime_state"
        payload = {"gate": "2", "bullet": 5, "run": RUN_TOKEN}
        try:
            await cache.set(key, payload, ttl_seconds=120)

            # Read back through the production path, and prove the value landed
            # in Redis rather than in the in-process fallback dict.
            assert await cache.get(key) == payload
            assert await client.exists(f"cache:{key}") == 1
            assert cache._cache == {}, (
                "the runtime-state write went to the in-process fallback, not to "
                "Redis; this test would then pass with the engine switched off"
            )

            ttl = int(await client.ttl(f"cache:{key}"))
            assert 0 < ttl <= 120

            assert await cache.delete(key) is True
            assert await cache.get(key) is None
        finally:
            await client.delete(f"cache:{key}")


async def test_runtime_state_ttl_actually_expires_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expiry is proven by waiting for it, not by reading back the TTL.

    Sessions, rate-limit windows and upload slots all depend on Redis dropping
    the key on its own. A `TTL` read only proves the number was accepted.
    """
    url = _url_with_db(_require_live_redis(), RUNTIME_STATE_DB)
    async with _runtime_state(url) as service:
        client = await service.get_redis()
        assert client is not None
        cache = InMemoryCache()
        monkeypatch.setattr(cache, "_runtime_state", service, raising=True)

        key = f"{KEY_NAMESPACE}_expiring"
        try:
            await cache.set(key, "transient", ttl_seconds=1)
            assert await cache.get(key) == "transient"

            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10
            expired = False
            while loop.time() < deadline:
                if await cache.get(key) is None:
                    expired = True
                    break
                await asyncio.sleep(0.25)
            assert expired, "a 1-second runtime-state key was still readable after 10 seconds"
            assert await client.exists(f"cache:{key}") == 0
        finally:
            await client.delete(f"cache:{key}")


# --------------------------------------------------------------------------- #
# 3. Queue: the primitive production actually uses
# --------------------------------------------------------------------------- #


async def test_contract_queue_enqueue_and_dequeue_live(monkeypatch: pytest.MonkeyPatch) -> None:
    url = _url_with_db(_require_live_redis(), QUEUE_DB)
    async with _queue(url, monkeypatch) as (queue, ready, processing):
        client = await queue.connect()
        assert client is not None, (
            f"ContractIngestQueue could not reach or authenticate against "
            f"{REDIS_TEST_URL_ENV} db {QUEUE_DB}"
        )
        assert queue.redis_db_number == QUEUE_DB

        job_id = f"{KEY_NAMESPACE}_job"
        returned = await queue.enqueue({"upload_id": job_id, "organization_id": "gate2"})
        assert returned == job_id

        # The job record and the ready list, exactly as the worker expects them.
        metadata = await client.hgetall(queue._job_key(job_id))
        assert metadata.get("status") == "queued"
        assert await client.lrange(ready, 0, -1) == [job_id]

        # Enqueue is idempotent while the job is still queued: no second entry.
        # Duplicated ingestion is the failure this guards - the same upload
        # OCR'd twice bills twice and races itself.
        assert await queue.enqueue({"upload_id": job_id}) == job_id
        assert await client.lrange(ready, 0, -1) == [job_id]

        # The dequeue primitive is BRPOPLPUSH onto the processing list, which is
        # what makes a crashed worker recoverable. A plain BRPOP would lose the
        # job, so this is asserted against the real command.
        popped = await client.brpoplpush(ready, processing, timeout=5)
        assert popped == job_id
        assert await client.lrange(ready, 0, -1) == []
        assert await client.lrange(processing, 0, -1) == [job_id]


async def test_contract_queue_recovers_a_stale_processing_job_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visibility timeout: a job whose worker died returns to the ready list.

    This is the production recovery path (`_recover_stale_processing_jobs_once`),
    not a re-implementation of it. Without it a worker crash strands the upload
    in `processing` forever and the document never finishes ingesting.
    """
    url = _url_with_db(_require_live_redis(), QUEUE_DB)
    async with _queue(url, monkeypatch) as (queue, ready, processing):
        client = await queue.connect()
        assert client is not None

        job_id = f"{KEY_NAMESPACE}_stale"
        await client.hset(
            queue._job_key(job_id),
            mapping={
                "payload": "{}",
                "status": "running",
                "attempts": "1",
                # Older than any permitted visibility timeout (the setting has a
                # floor of 60 seconds and the property clamps to it).
                "heartbeat_at": "2000-01-01T00:00:00",
                "updated_at": "2000-01-01T00:00:00",
            },
        )
        await client.rpush(processing, job_id)

        recovered = await queue._recover_stale_processing_jobs_once(client)

        assert recovered == 1
        assert await client.lrange(processing, 0, -1) == []
        assert await client.lrange(ready, 0, -1) == [job_id]
        metadata = await client.hgetall(queue._job_key(job_id))
        assert metadata.get("status") == "queued"
        assert metadata.get("recovery_reason") == "visibility_timeout"


# --------------------------------------------------------------------------- #
# Negative controls - the suite must be able to fail
# --------------------------------------------------------------------------- #


async def test_the_queue_assertions_are_value_sensitive_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong queue behaviour must be RED, not merely "some job came back".

    The positive test above asserts identities. This proves those assertions
    measure the configured list and the configured job rather than accepting
    whatever the engine happens to return: a job enqueued onto one queue is not
    visible on another, and a dequeue from an empty ready list yields nothing
    instead of an unrelated id.
    """
    url = _url_with_db(_require_live_redis(), QUEUE_DB)
    async with _queue(url, monkeypatch) as (queue, ready, processing):
        client = await queue.connect()
        assert client is not None

        job_id = f"{KEY_NAMESPACE}_scoped"
        await queue.enqueue({"upload_id": job_id})

        other_ready = _queue_key("other_ready")
        other_processing = _queue_key("other_processing")
        assert await client.lrange(other_ready, 0, -1) == [], (
            "a job enqueued onto one queue is visible on another; the queue-name "
            "assertions above would pass against the wrong list"
        )
        assert await client.brpoplpush(other_ready, other_processing, timeout=1) is None

        # And the real list yields exactly the job that was enqueued, not an
        # arbitrary member.
        assert await client.brpoplpush(ready, processing, timeout=5) == job_id
        assert await client.brpoplpush(ready, processing, timeout=1) is None


async def test_wrong_port_is_refused_live() -> None:
    """Point the production adapter at a port nothing serves; it must not report a client."""
    parsed = urlsplit(_require_live_redis())
    auth = f":{parsed.password}@" if parsed.password else ""
    # A port in the IANA dynamic range that this stack never binds.
    broken = _url_with_netloc(parsed.geturl(), f"{auth}{parsed.hostname}:59999")
    async with _runtime_state(broken) as service:
        assert await service.get_redis() is None, (
            "RuntimeStateService reported a usable client against a port nothing "
            "serves, so the reachability assertion in this module proves nothing"
        )


async def test_wrong_password_is_refused_live() -> None:
    """A wrong password must be a failure, not a fallback."""
    parsed = urlsplit(_require_live_redis())
    if not parsed.password:
        # Not a skip under the gate. This module is in
        # `staging_gate.GATE2_REQUIRED_LIVE_FILES`, where a skip is converted
        # into a failure - so skipping here would fail the run with a message
        # about a missing control rather than about the actual problem, which
        # is that the engine is unprotected. Production and staging both start
        # Redis with `--requirepass` (docker-compose.prod.yml), so a
        # passwordless endpoint is not the engine Gate 2 claims to have
        # measured.
        message = (
            "the Redis endpoint carries no password. Production runs Redis with "
            "--requirepass, so an unauthenticated endpoint cannot carry Gate 2 "
            "bullet 5 evidence and authentication cannot be negatively controlled."
        )
        if staging_gate.staging_gate_mode():
            pytest.fail(message)
        pytest.skip(f"{message} Developer mode: skipped.")
    netloc = f":not-the-staging-password@{parsed.hostname}:{parsed.port or 6379}"
    async with _runtime_state(_url_with_netloc(parsed.geturl(), netloc)) as service:
        assert await service.get_redis() is None, (
            "RuntimeStateService authenticated with the wrong password; the "
            "engine is unprotected and no Gate 2 credential claim holds"
        )


# --------------------------------------------------------------------------- #
# Residue - asserted last, because a shared engine is not ours to litter
# --------------------------------------------------------------------------- #


async def test_this_run_left_no_redis_residue_live() -> None:
    base = _require_live_redis()
    survivors: List[str] = []
    for db in (QUEUE_DB, RUNTIME_STATE_DB):
        async with _runtime_state(_url_with_db(base, db)) as service:
            client = await service.get_redis()
            assert client is not None
            found = await _scan_run_keys(client)
            if found:
                await client.delete(*found)
                survivors.extend(f"db{db}:{key}" for key in found)
    assert not survivors, (
        f"Gate 2 Redis run {RUN_TOKEN} left keys behind on a shared engine "
        f"(now deleted): {survivors}"
    )
