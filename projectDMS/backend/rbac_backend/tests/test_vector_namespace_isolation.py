"""A namespace is chosen per operation; the configured default never moves.

``get_vector_client()`` is one process-wide ``VectorClient`` shared by
retrieval, ingestion, the clause embedder and the contract projection. Its
``_ensure_collection`` used to assign the namespace a call named - and on a
dimension mismatch the ``<name>_dim<N>`` fallback, plus the vector name it
detected there - to the client and to the shared config. One explicit-
namespace call (a ``contract_clauses`` upsert, even a failed one) then sent
every later default call - generic ingestion, reconcile, retrieval - to that
namespace.

Real Qdrant (``CORRESPONDENCE_QDRANT_TEST_URL``) is the point; the local
in-memory client runs the cases it can.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import uuid
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.ingestion.chunk_ids import deterministic_chunk_id
from rbac_backend.retrieval.point_ids import generic_chunk_point_id
from rbac_backend.retrieval.vector_client import VectorClient

QDRANT_SERVER_ENV = "CORRESPONDENCE_QDRANT_TEST_URL"
DIMENSIONS = 32
ORG, PROJECT = str(ObjectId()), str(ObjectId())

BACKENDS = [
    "local",
    pytest.param(
        "server",
        marks=pytest.mark.skipif(
            not os.environ.get(QDRANT_SERVER_ENV),
            reason=f"{QDRANT_SERVER_ENV} not set",
        ),
    ),
]


def _vector(text: str) -> List[float]:
    vector = [0.0] * DIMENSIONS
    for token in text.lower().split():
        digest = hashlib.sha256(token.encode()).digest()
        vector[digest[0] % DIMENSIONS] += 1.0
    norm = sum(v * v for v in vector) ** 0.5 or 1.0
    return [v / norm for v in vector]


class _Serialized:
    """One lock around every call into a wrapped client."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                return attr(*args, **kwargs)

        return call


class Store:
    """A real ``VectorClient`` (built through ``__init__``) over a fresh default
    collection, and the raw Qdrant client to look behind it."""

    def __init__(self, backend: str) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm

        self.backend = backend
        self.models = qm
        self.tag = uuid.uuid4().hex[:10]
        config = DocumentProcessingConfig()
        config.qdrant_api_key = None
        config.qdrant_collection = f"ns-default-{self.tag}"
        config.qdrant_vector_size = DIMENSIONS
        config.qdrant_distance = "cosine"
        config.qdrant_vector_name = None
        if backend == "server":
            config.qdrant_url = os.environ[QDRANT_SERVER_ENV]
            self.vc = VectorClient(config)
        else:
            config.qdrant_url = None
            self.vc = VectorClient(config)
            # The in-memory client is not thread-safe; the concurrency under
            # test is VectorClient's own (its collection is chosen before the
            # store call is handed to a thread).
            self.vc._client = _Serialized(QdrantClient(location=":memory:"))
            self.vc._qmodels = qm
            self.vc.enabled = True
        assert self.vc.enabled
        self.config = config
        self.default = config.qdrant_collection
        self.created: List[str] = []

    @property
    def client(self):
        return self.vc._client

    def name(self, stem: str) -> str:
        return f"{stem}-{self.tag}"

    def collection_names(self) -> set[str]:
        return {c.name for c in self.client.get_collections().collections}

    def chunk_ids(self, collection: str) -> List[str]:
        if collection not in self.collection_names():
            return []
        points, _ = self.client.scroll(
            collection_name=collection, limit=1000, with_payload=True, with_vectors=False
        )
        return sorted(str((p.payload or {}).get("chunk_id")) for p in points)

    def drop(self) -> None:
        for name in list(self.collection_names()):
            if self.tag in name:
                try:
                    self.client.delete_collection(name)
                except Exception:  # pragma: no cover - cleanup best effort
                    pass


@pytest.fixture(params=BACKENDS)
def store(request):
    built = Store(request.param)
    yield built
    built.drop()


def _generic_chunk(text: str) -> Dict[str, Any]:
    document_id = str(ObjectId())
    return {
        "chunk_id": deterministic_chunk_id(document_id, 0, None, text),
        "document_id": document_id,
        "org_id": ORG,
        "project_id": PROJECT,
        "text": text,
    }


def _contract_chunk(text: str) -> Dict[str, Any]:
    """Contract Master / contract ingest identity: a UUID5 point id."""
    document_id = str(ObjectId())
    return {
        "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"upload:clause-1:0:{document_id}")),
        "document_id": document_id,
        "org_id": ORG,
        "project_id": PROJECT,
        "text": text,
    }


async def _generic_upsert(vc: VectorClient, chunk: Dict[str, Any]) -> int:
    return await vc.upsert(
        [_vector(chunk["text"])], [chunk], point_id_for=generic_chunk_point_id
    )


async def _explicit_upsert(
    vc: VectorClient, namespace: str, chunk: Dict[str, Any]
) -> int:
    return await vc.upsert([_vector(chunk["text"])], [chunk], namespace=namespace)


def _scope(chunk: Dict[str, Any]) -> Dict[str, Any]:
    return {"org_id": ORG, "project_id": PROJECT, "document_id": chunk["document_id"]}


def _assert_default_untouched(store: Store) -> None:
    assert store.config.qdrant_collection == store.default
    assert store.config.qdrant_vector_name is None


# --- single calls --------------------------------------------------------------------


def test_a_default_write_lands_in_the_default_collection(store: Store) -> None:
    chunk = _generic_chunk("default write")

    async def go() -> Optional[str]:
        assert await _generic_upsert(store.vc, chunk) == 1
        return store.vc.collection_name

    assert asyncio.run(go()) == store.default
    assert store.chunk_ids(store.default) == [chunk["chunk_id"]]
    _assert_default_untouched(store)


def test_an_explicit_write_lands_in_its_namespace_only(store: Store) -> None:
    clauses = store.name("contract_clauses")
    chunk = _contract_chunk("clause write")

    async def go() -> Optional[str]:
        assert await _explicit_upsert(store.vc, clauses, chunk) == 1
        return store.vc.collection_name

    assert asyncio.run(go()) == clauses
    assert store.chunk_ids(clauses) == [chunk["chunk_id"]]
    assert store.chunk_ids(store.default) == []
    _assert_default_untouched(store)


def test_a_default_call_after_an_explicit_one_returns_to_the_default(store: Store) -> None:
    clauses = store.name("contract_clauses")
    clause = _contract_chunk("clause first")
    generic = _generic_chunk("generic after")

    asyncio.run(_explicit_upsert(store.vc, clauses, clause))
    asyncio.run(_generic_upsert(store.vc, generic))

    assert store.chunk_ids(store.default) == [generic["chunk_id"]]
    assert store.chunk_ids(clauses) == [clause["chunk_id"]]
    # Every default read follows the default too.
    assert asyncio.run(store.vc.list_chunk_ids(_scope(generic))) == [generic["chunk_id"]]
    hits = asyncio.run(store.vc.search(_vector("generic after"), {"org_id": ORG}, limit=10))
    assert [h["payload"]["chunk_id"] for h in hits] == [generic["chunk_id"]]
    assert asyncio.run(store.vc.delete([generic["chunk_id"]], point_id_for=generic_chunk_point_id))
    assert store.chunk_ids(store.default) == []
    assert store.chunk_ids(clauses) == [clause["chunk_id"]]
    _assert_default_untouched(store)


def test_a_failed_clause_upsert_cannot_poison_the_default(store: Store) -> None:
    """The clause embedder's SHA-1 ``clause_uid`` is not a Qdrant point id; a
    real store refuses it (recorded residual R2). The refusal must not move
    the default: the next generic, mapped write still lands there."""
    if store.backend != "server":
        pytest.skip("only a real Qdrant is certified to refuse the SHA-1 id")
    clauses = store.name("contract_clauses")
    sha1 = hashlib.sha1(b"clause-uid").hexdigest()
    rejected = dict(_contract_chunk("sha1 clause"), chunk_id=sha1)

    with pytest.raises(Exception):
        asyncio.run(_explicit_upsert(store.vc, clauses, rejected))

    generic = _generic_chunk("after the refusal")
    asyncio.run(_generic_upsert(store.vc, generic))
    assert store.chunk_ids(store.default) == [generic["chunk_id"]]
    assert store.chunk_ids(clauses) == []
    _assert_default_untouched(store)


def test_a_refused_explicit_upsert_cannot_poison_the_default(store: Store) -> None:
    """The same isolation on every backend: the store refuses the explicit
    write, and the default holds."""
    clauses = store.name("contract_clauses")
    store.client.create_collection(
        collection_name=clauses,
        vectors_config=store.models.VectorParams(
            size=DIMENSIONS, distance=store.models.Distance.COSINE
        ),
    )
    original = store.client.upsert

    def refuse_clauses(*args: Any, **kwargs: Any):
        if kwargs.get("collection_name", "").startswith("contract_clauses"):
            raise RuntimeError("400: invalid point id")
        return original(*args, **kwargs)

    store.client.upsert = refuse_clauses
    try:
        with pytest.raises(RuntimeError):
            asyncio.run(_explicit_upsert(store.vc, clauses, _contract_chunk("refused")))
        generic = _generic_chunk("after the refusal")
        asyncio.run(_generic_upsert(store.vc, generic))
    finally:
        store.client.upsert = original

    assert store.chunk_ids(store.default) == [generic["chunk_id"]]
    assert store.chunk_ids(clauses) == []
    _assert_default_untouched(store)


# --- concurrency and cross-talk ---------------------------------------------------------


def test_concurrent_default_and_explicit_calls_stay_in_their_own_collections(
    store: Store,
) -> None:
    clauses = store.name("contract_clauses")
    generic = [_generic_chunk(f"generic {i}") for i in range(6)]
    contract = [_contract_chunk(f"contract {i}") for i in range(6)]

    async def default_task(chunk: Dict[str, Any]) -> Optional[str]:
        await _generic_upsert(store.vc, chunk)
        return store.vc.collection_name

    async def explicit_task(chunk: Dict[str, Any]) -> Optional[str]:
        await _explicit_upsert(store.vc, clauses, chunk)
        return store.vc.collection_name

    async def go() -> List[Optional[str]]:
        tasks = []
        for g, c in zip(generic, contract):  # interleaved: explicit between defaults
            tasks.append(default_task(g))
            tasks.append(explicit_task(c))
        return await asyncio.gather(*tasks)

    reported = asyncio.run(go())

    assert store.chunk_ids(store.default) == sorted(c["chunk_id"] for c in generic)
    assert store.chunk_ids(clauses) == sorted(c["chunk_id"] for c in contract)
    # Each task's report names the collection its own write used.
    assert reported == [store.default, clauses] * len(generic)
    _assert_default_untouched(store)


def test_two_explicit_namespaces_do_not_cross_talk(store: Store) -> None:
    first, second = store.name("ns-a"), store.name("ns-b")
    a, b = _contract_chunk("alpha text"), _contract_chunk("beta text")

    asyncio.run(_explicit_upsert(store.vc, first, a))
    asyncio.run(_explicit_upsert(store.vc, second, b))

    assert store.chunk_ids(first) == [a["chunk_id"]]
    assert store.chunk_ids(second) == [b["chunk_id"]]
    hits = asyncio.run(
        store.vc.search(_vector("alpha text"), {"org_id": ORG}, limit=10, namespace=first)
    )
    assert [h["payload"]["chunk_id"] for h in hits] == [a["chunk_id"]]
    assert asyncio.run(store.vc.list_chunk_ids(_scope(b), namespace=first)) == []
    assert asyncio.run(store.vc.list_chunk_ids(_scope(b), namespace=second)) == [b["chunk_id"]]

    asyncio.run(store.vc.delete([b["chunk_id"]], namespace=first))
    assert store.chunk_ids(second) == [b["chunk_id"]]
    asyncio.run(store.vc.delete([b["chunk_id"]], namespace=second))
    assert store.chunk_ids(second) == []
    assert store.chunk_ids(first) == [a["chunk_id"]]
    assert store.chunk_ids(store.default) == []
    _assert_default_untouched(store)


# --- the dimension fallback and the vector name are per collection ------------------------


def test_an_explicit_dimension_fallback_stays_with_its_namespace(store: Store) -> None:
    """A mismatched explicit collection falls back to ``<name>_dim<N>`` for that
    namespace's own calls; the report says so (the contract projection refuses
    a fallback write on it), and the default never moves."""
    evidence = store.name("document_vectors")
    store.client.create_collection(
        collection_name=evidence,
        vectors_config=store.models.VectorParams(size=8, distance=store.models.Distance.COSINE),
    )
    chunk = _contract_chunk("evidence write")

    async def go() -> Optional[str]:
        await _explicit_upsert(store.vc, evidence, chunk)
        return store.vc.collection_name

    fallback = f"{evidence}_dim{DIMENSIONS}"
    assert asyncio.run(go()) == fallback
    assert store.chunk_ids(fallback) == [chunk["chunk_id"]]
    # The namespace's own reads follow its fallback.
    assert asyncio.run(store.vc.list_chunk_ids(_scope(chunk), namespace=evidence)) == [
        chunk["chunk_id"]
    ]

    generic = _generic_chunk("default after fallback")
    asyncio.run(_generic_upsert(store.vc, generic))
    assert store.chunk_ids(store.default) == [generic["chunk_id"]]
    assert asyncio.run(store.vc.list_chunk_ids(_scope(generic))) == [generic["chunk_id"]]
    _assert_default_untouched(store)


def test_a_vector_name_detected_in_one_namespace_does_not_leak_into_the_default(
    store: Store,
) -> None:
    named = store.name("named")
    store.client.create_collection(
        collection_name=named,
        vectors_config={
            "dense": store.models.VectorParams(
                size=DIMENSIONS, distance=store.models.Distance.COSINE
            )
        },
    )
    a = _contract_chunk("named vector")
    asyncio.run(_explicit_upsert(store.vc, named, a))
    # Written under the vector name that collection has. (A named-vector
    # *search* is a separate, pre-existing query-shape defect; not tested here.)
    assert store.chunk_ids(named) == [a["chunk_id"]]

    generic = _generic_chunk("unnamed default")
    asyncio.run(_generic_upsert(store.vc, generic))
    hits = asyncio.run(store.vc.search(_vector("unnamed default"), {"org_id": ORG}, limit=10))
    assert [h["payload"]["chunk_id"] for h in hits] == [generic["chunk_id"]]
    _assert_default_untouched(store)


# --- the contract writers keep their identities ----------------------------------------------


def test_a_contract_master_uuid5_write_is_unchanged_and_reported_per_task(
    store: Store,
) -> None:
    """The projection writes UUID5 ids to its named evidence namespace and then
    checks the client's report; a concurrent default write in another task
    must not change what this task reads back."""
    evidence = store.name("document_vectors")
    chunk = _contract_chunk("projection point")
    generic = _generic_chunk("concurrent default")

    async def projection() -> Optional[str]:
        await _explicit_upsert(store.vc, evidence, chunk)
        await asyncio.sleep(0.05)  # the default write runs here
        return store.vc.collection_name

    async def go():
        return await asyncio.gather(projection(), _generic_upsert(store.vc, generic))

    reported, _ = asyncio.run(go())

    assert reported == evidence
    points, _ = store.client.scroll(collection_name=evidence, limit=10, with_payload=True)
    assert [str(p.id) for p in points] == [chunk["chunk_id"]]
    assert store.chunk_ids(store.default) == [generic["chunk_id"]]
    _assert_default_untouched(store)


def test_the_clause_sha1_point_id_is_still_refused_by_real_qdrant(store: Store) -> None:
    """Residual R2 stays a refusal: the clause writer is not enabled here and its
    id scheme is not silently accepted."""
    if store.backend != "server":
        pytest.skip("only a real Qdrant validates point ids this way")
    clauses = store.name("contract_clauses")
    sha1 = hashlib.sha1(b"clause-uid").hexdigest()
    with pytest.raises(Exception) as refused:
        asyncio.run(
            _explicit_upsert(store.vc, clauses, dict(_contract_chunk("x"), chunk_id=sha1))
        )
    assert "400" in str(refused.value) or "format" in str(refused.value).lower()
    _assert_default_untouched(store)
