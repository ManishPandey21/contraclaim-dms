"""Builds one Contract Master projection generation from the canonical source.

The reprojection worker owns *when* and *under which fence*; this module owns
*what*. Given a promoted instrument at classification revision N it:

1. resolves the canonical Document and refuses one that may not publish;
2. loads the accepted page text the extraction persisted (``contract_ocr_pages``);
3. chunks it with the **same** clause pipeline upload ingestion uses
   (``ContractIngestor.extract_clause_set`` / ``_build_clause_payloads``);
4. embeds every chunk with ``strict=True`` - no deterministic fallback vector can
   certify a generation;
5. publishes every point to the configured vector store, with the same point
   ids the upload wrote, so a rebuild overwrites instead of duplicating;
6. syncs the clause graph exactly as upload does (best effort - the evidence
   contract treats the graph as a degradable expansion source);
7. returns the ``document_vectors`` rows, each stamped with
   ``source_classification_revision = N``.

It does **not** write those rows and it never touches the instrument. The rows
reach Mongo only inside the worker's completion transaction, together with the
ownership check and the ``classification_revision == N`` fence, so a row set and
its CURRENT stamp cannot exist without each other.

What this module can never write: organisation ownership, project anchor,
``scope_level``, applicability, legal effect, classification facts. It reads the
canonical Document for provenance and writes derived stores only.

Why the persisted page text rather than re-running OCR: the projection must
describe the extraction that was accepted (and, for a held contract, reviewed),
not a fresh OCR pass that may read differently and that meters OCR again. A
contract with no persisted page text cannot be projected here and FAILS visibly
- the fix for it is the reindex path, never a silent success.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .publication_policy import is_publication_blocked, resolve_canonical_document

logger = logging.getLogger(__name__)

__all__ = [
    "BuiltProjection",
    "ContractProjectionBuilder",
    "ProjectionUnavailable",
]


#: A writer is running. ``queued`` / ``retrying`` are the general queue's
#: bookkeeping: it writes them before its worker refuses a governed contract and
#: nothing settles them again, so treating them as "in flight" blocked the
#: projection forever. A contract ingest is detected by the source lease; this
#: remains a second, independent signal for a write actually under way.
_WRITING_STATES = frozenset({"processing"})


def _ingest_in_flight(document: Dict[str, Any]) -> bool:
    return (
        str(document.get("status") or "") in _WRITING_STATES
        or str(document.get("processing_status") or "") in _WRITING_STATES
    )


#: The Qdrant namespace Contract Master evidence searches: the name of the
#: lexical collection (``contract_service`` passes ``collection.name``).
EVIDENCE_VECTOR_NAMESPACE = "document_vectors"


class ProjectionUnavailable(Exception):
    """This generation cannot be built right now.

    Always recorded as a visible FAILED projection with this message; never
    converted into an empty-but-successful one.
    """


@dataclass
class BuiltProjection:
    document_id: str
    revision: int
    rows: List[Dict[str, Any]]
    vector_points: int
    graph_status: str
    clause_source: str
    notes: List[str] = field(default_factory=list)
    #: The settled source generation the rows were built from; re-checked in
    #: the completion transaction (``publish_rows``).
    source_key: str = ""
    source_generation: int = 0


class ContractProjectionBuilder:
    """Rebuilds the evidence projection of one instrument at one revision."""

    def __init__(
        self,
        db: Any,
        *,
        ingestor: Any = None,
        embedder: Any = None,
        vector_store: Any = None,
        graph: Any = None,
    ) -> None:
        self._db = db
        self._ingestor = ingestor
        self._embedder = embedder
        self._vector_store = vector_store
        self._graph = graph

    # -- collaborators ------------------------------------------------------ #

    @property
    def ingestor(self) -> Any:
        if self._ingestor is None:
            from .contracts_ingest import create_contract_ingestor

            self._ingestor = create_contract_ingestor(self._db)
        return self._ingestor

    @property
    def embedder(self) -> Any:
        return self._embedder if self._embedder is not None else self.ingestor.embedding_client

    @property
    def vector_store(self) -> Any:
        return (
            self._vector_store if self._vector_store is not None else self.ingestor.vector_client
        )

    @property
    def graph(self) -> Any:
        return self._graph if self._graph is not None else self.ingestor.contract_graph

    # -- build -------------------------------------------------------------- #

    async def build(
        self,
        instrument: Dict[str, Any],
        *,
        revision: int,
        heartbeat: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> BuiltProjection:
        document_id = str(instrument.get("document_id") or "")
        if not document_id:
            raise ProjectionUnavailable("instrument names no canonical document")

        document = await resolve_canonical_document(self._db, document_id)
        if document is None:
            raise ProjectionUnavailable(f"canonical document {document_id} does not exist")
        if is_publication_blocked(document):
            raise ProjectionUnavailable(
                f"canonical document {document_id} may not publish (held for review, "
                "quarantined, unpublished or inactive); a projection of it would be "
                "evidence the publication policy forbids"
            )

        if _ingest_in_flight(document):
            # Publication policy keeps last-known-good content consumable while
            # a run is in flight; a projection must not be BUILT from it, since
            # the page text and rows are being rewritten underneath. The ingest
            # re-opens this generation when it settles.
            raise ProjectionUnavailable(
                f"canonical document {document_id} is being re-ingested; its "
                "projection is rebuilt when the ingest settles"
            )

        # The durable source lease, not documents.status, is what says whether
        # an ingest is writing: two ingests overwrite each other's status, and a
        # crashed one leaves whatever it last wrote.
        from .contract_source_lease import source_state

        source_key = str(document["_id"])
        source = await source_state(self._db, source_key)
        if not source.projectable:
            raise ProjectionUnavailable(
                f"the source of {document_id} is {source.status}: an ingest is writing "
                "it, or the last one stopped part-way. The projection is rebuilt when "
                "an ingest settles it"
            )

        ingestor = self.ingestor
        parsed = await self._load_source(document_id, document)
        clauses, clause_source = await ingestor.extract_clause_set(parsed.text)

        organization_id = str(document.get("organization_id") or instrument.get("organization_id") or "")
        project_id = str(document.get("project_id") or "") or None
        filename = str(document.get("filename") or document.get("original_filename") or "contract")
        upload_id = str(
            document.get("contract_upload_id") or document.get("upload_id") or document_id
        )
        source_path = str(document.get("filepath_local") or document.get("file_path") or filename)
        tags = [str(tag) for tag in (document.get("tags") or []) if tag]

        payloads = ingestor._build_clause_payloads(
            parsed,
            clauses,
            tags,
            upload_id,
            document_id,
            organization_id,
            project_id,
            filename,
            source_path,
            map_pages=True,
            clause_source=clause_source,
        )
        if not payloads:
            raise ProjectionUnavailable(
                f"no clause chunks were produced from {len(parsed.pages)} page(s) of "
                f"{document_id}; an empty projection is not a current one"
            )
        for payload in payloads:
            payload["metadata"]["source_classification_revision"] = int(revision)

        vector_store = self.vector_store
        if not getattr(vector_store, "enabled", False):
            raise ProjectionUnavailable(
                "the vector store is not configured or not reachable; the evidence "
                "vector source would be empty for this generation"
            )

        embeddings = await self._embed(payloads, heartbeat=heartbeat)

        # Publication authority is re-read immediately before the first derived
        # write: a hold placed while we were embedding must stop us.
        from .contracts_ingest import IngestionError

        try:
            await ingestor._assert_current_publication_authority(document_id)
        except IngestionError as exc:
            raise ProjectionUnavailable(str(exc)) from exc
        vector_points = await self._publish_vectors(payloads, embeddings, heartbeat=heartbeat)
        await self._remove_orphan_points(
            payloads,
            document_id=document_id,
            organization_id=organization_id,
            heartbeat=heartbeat,
            source_key=source_key,
            source_generation=source.generation,
        )
        if heartbeat is not None:
            # The graph write cannot join the completion transaction either.
            await heartbeat()

        graph_status = await asyncio.to_thread(
            self._sync_graph,
            payloads,
            document_id=document_id,
            filename=filename,
            organization_id=organization_id,
            project_id=project_id,
        )
        logger.info("reprojection graph sync for %s: %s", document_id, graph_status)

        rows = ingestor._create_vector_records(
            [
                {
                    "metadata": payload["metadata"],
                    "text": payload["text"],
                    "embedding": embedding,
                }
                for payload, embedding in zip(payloads, embeddings)
            ]
        )
        for row in rows:
            row["source_classification_revision"] = int(revision)
            # The vector lives in the vector store, which is what evidence
            # searches. Keeping it off the lexical row keeps the publication
            # transaction small enough for large contracts.
            row.pop("embedding", None)
            row["embedding_dims"] = len(embeddings[0]) if embeddings else 0

        return BuiltProjection(
            document_id=document_id,
            revision=int(revision),
            rows=rows,
            vector_points=vector_points,
            graph_status=graph_status,
            clause_source=clause_source,
            source_key=source_key,
            source_generation=source.generation,
        )

    async def publish_rows(self, built: BuiltProjection, session: Any) -> None:
        """Replace the document's lexical rows, inside the completion transaction.

        Every contract row of the document is replaced, whatever generation wrote
        it: the transaction only commits while the instrument is still at
        ``built.revision``, so the set left behind is always the current one.

        The source generation the rows were built from must still be the settled
        one, read inside the same transaction: an ingest that started (or ran
        and settled) since the build read the page text makes these rows a
        description of text that is no longer stored.
        """
        from .contract_source_lease import source_state

        if built.source_key:
            source = await source_state(self._db, built.source_key, session=session)
            if not source.projectable or source.generation != built.source_generation:
                raise ProjectionUnavailable(
                    f"the source of {built.document_id} changed after this generation "
                    f"was built (generation {built.source_generation} -> "
                    f"{source.generation}, {source.status}); it is rebuilt from the new source"
                )
            # A read in a snapshot transaction does not conflict with a write
            # that commits after it: an ingest acquiring the lease, or a taint,
            # would slip past. Writing the lease row makes either of them a
            # write conflict with this publication, so one of the two aborts.
            from pymongo.errors import DuplicateKeyError

            from .contract_source_lease import CONTRACT_SOURCE_LEASES_COLLECTION, SETTLED

            try:
                touched = await self._db[CONTRACT_SOURCE_LEASES_COLLECTION].update_one(
                    {"_id": built.source_key, "status": SETTLED, "generation": built.source_generation},
                    {"$set": {"published_revision": built.revision}},
                    upsert=built.source_generation == 0,
                    session=session,
                )
            except DuplicateKeyError as exc:
                raise ProjectionUnavailable(
                    f"the source of {built.document_id} was taken by an ingest during publication"
                ) from exc
            if not getattr(touched, "matched_count", 0) and not getattr(touched, "upserted_id", None):
                raise ProjectionUnavailable(
                    f"the source of {built.document_id} changed during publication; it is "
                    "rebuilt from the new source"
                )
        collection = self._db.document_vectors
        await collection.delete_many(
            {"uploadType": "contract", "document_id": built.document_id}, session=session
        )
        # Copies: a retried transaction must not re-insert dicts the driver has
        # already stamped with an ``_id``.
        await collection.insert_many([dict(row) for row in built.rows], session=session)

    # -- steps -------------------------------------------------------------- #

    async def _load_source(self, document_id: str, document: Dict[str, Any]):
        from .contracts_ingest import ContractIngestor, ParsedPage

        filename = str(document.get("filename") or document.get("original_filename") or "")
        rows = await self._db.contract_ocr_pages.find({"document_id": document_id}).to_list(
            length=None
        )
        names = [filename, str(document.get("filepath_local") or ""), str(document.get("file_path") or "")]
        is_pdf = any(name.lower().endswith(".pdf") for name in names if name)
        if not rows and filename and not is_pdf:
            # Text/DOCX have no page store: ingest parses the stored file with no
            # OCR, and so does this - the same parser and the same cleaner. The
            # accepted page store always wins when it exists.
            return await self._load_stored_text_file(document_id, document, filename)

        by_page: Dict[int, str] = {}
        for row in rows:
            number = int(row.get("page_number") or 0)
            if number <= 0:
                continue
            by_page[number] = str(row.get("cleaned_text") or row.get("text") or "")
        if not by_page:
            raise ProjectionUnavailable(
                f"no persisted page text for {document_id}; the projection is built "
                "from the accepted extraction, so this contract needs a reindex "
                "before it can become evidence"
            )
        pages = [
            ParsedPage(number=number, text=by_page[number], start=0, end=0)
            for number in sorted(by_page)
        ]
        parsed = ContractIngestor._combine_pages(
            pages, Path(str(document.get("filename") or "contract.pdf"))
        )
        if not parsed.text.strip():
            raise ProjectionUnavailable(f"the persisted page text of {document_id} is empty")
        return parsed

    async def _load_stored_text_file(
        self, document_id: str, document: Dict[str, Any], filename: str
    ):
        ingestor = self.ingestor
        local = document.get("filepath_local") or document.get("file_path")
        path: Optional[Path] = None
        temp = False
        try:
            if document.get("file_object_id"):
                from .file_object_service import FileObjectService

                path = await FileObjectService().materialize_to_temp(
                    str(document["file_object_id"]), suffix=Path(filename).suffix
                )
                temp = True
            elif local and Path(str(local)).exists():
                path = Path(str(local))
            if path is None:
                raise ProjectionUnavailable(
                    f"the stored file of {document_id} is not available to rebuild its projection"
                )
            raw = await ingestor.parser.extract_text(path)
            parsed = (
                ingestor.text_preprocessor.clean_document(raw)[0]
                if ingestor.processing_config.contract_text_cleaning_enabled
                else raw
            )
        except ProjectionUnavailable:
            raise
        except Exception as exc:
            raise ProjectionUnavailable(f"could not read the stored file of {document_id}: {exc}") from exc
        finally:
            if temp and path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass
        if not parsed.text.strip():
            raise ProjectionUnavailable(f"the stored file of {document_id} has no text")
        return parsed

    async def _embed(
        self,
        payloads: List[Dict[str, Any]],
        *,
        heartbeat: Optional[Callable[[], Awaitable[None]]],
    ) -> List[List[float]]:
        from ..retrieval.embeddings import EmbeddingUnavailable

        ingestor = self.ingestor
        batch_size = max(1, int(ingestor.config.EMBEDDING_BATCH_SIZE))
        embeddings: List[List[float]] = []
        for start in range(0, len(payloads), batch_size):
            batch = payloads[start : start + batch_size]
            try:
                vectors = await self.embedder.embed(
                    [item.get("text") or "" for item in batch],
                    model=ingestor.config.EMBEDDING_MODEL,
                    strict=True,
                )
            except EmbeddingUnavailable as exc:
                raise ProjectionUnavailable(f"embedding unavailable: {exc}") from exc
            if len(vectors) != len(batch) or any(not vector for vector in vectors):
                raise ProjectionUnavailable(
                    f"embedder returned {len(vectors)} usable vectors for {len(batch)} chunks"
                )
            embeddings.extend(vectors)
            if heartbeat is not None:
                await heartbeat()
        return embeddings

    async def _publish_vectors(
        self,
        payloads: List[Dict[str, Any]],
        embeddings: List[List[float]],
        *,
        heartbeat: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> int:
        ingestor = self.ingestor
        batch_size = max(1, int(ingestor.config.EMBEDDING_BATCH_SIZE))
        written = 0
        for start in range(0, len(payloads), batch_size):
            batch = payloads[start : start + batch_size]
            vectors = embeddings[start : start + batch_size]
            chunks = [
                ingestor._build_qdrant_chunk(item.get("metadata") or {}, item.get("text") or "", len(vector))
                for item, vector in zip(batch, vectors)
            ]
            if heartbeat is not None:
                # Vector writes cannot join the completion transaction, so each
                # batch re-proves ownership and revision immediately before it.
                await heartbeat()
            try:
                # The evidence namespace, named explicitly: the store's configured
                # default collection (``QDRANT_COLLECTION``) is not what evidence
                # searches, and a projection written there would never be read.
                written += int(
                    await self.vector_store.upsert(
                        vectors, chunks, namespace=EVIDENCE_VECTOR_NAMESPACE
                    )
                    or 0
                )
            except Exception as exc:
                raise ProjectionUnavailable(f"vector publication failed: {exc}") from exc
        if written != len(payloads):
            raise ProjectionUnavailable(
                f"vector store accepted {written} of {len(payloads)} points; a partial "
                "vector projection is not a current one"
            )
        # The store may still have written elsewhere: the client's dimension-
        # mismatch fallback to ``<name>_dim<N>`` is a collection evidence never
        # reads - not a current projection.
        written_to = getattr(self.vector_store, "collection_name", None)
        if isinstance(written_to, str) and written_to != EVIDENCE_VECTOR_NAMESPACE:
            raise ProjectionUnavailable(
                f"vectors were written to collection {written_to!r}, but Contract "
                f"Master evidence searches {EVIDENCE_VECTOR_NAMESPACE!r}"
            )
        return written

    async def _remove_orphan_points(
        self,
        payloads: List[Dict[str, Any]],
        *,
        document_id: str,
        organization_id: str,
        heartbeat: Optional[Callable[[], Awaitable[None]]] = None,
        source_key: str = "",
        source_generation: int = 0,
    ) -> None:
        """Delete this document's points that the new generation did not write.

        Point ids carry the chunk checksum, so changed page text (an OCR retry)
        or a different chunking produces new ids; the old points would still be
        eligible (eligibility is per document) and would take ranking slots with
        nothing to hydrate. Failure here fails the build rather than publishing
        a projection with stale points beside it.

        "Did not write" is only true while this generation is still the owner:
        a newer generation that published after this build started has points
        this build does not know. So ownership and revision (``heartbeat``, an
        authoritative re-read) and the source generation are re-proved AFTER
        the orphans are listed and immediately BEFORE they are deleted. A newer
        generation can only publish after taking the claim over, which fails
        that re-check - so every point in the listed set predates it.
        """
        ingestor = self.ingestor
        store = self.vector_store
        delete = getattr(store, "delete", None)
        list_points = getattr(store, "list_points", None)
        list_ids = getattr(store, "list_chunk_ids", None)
        if delete is None or (list_points is None and list_ids is None):
            return
        keep = {
            ingestor._build_qdrant_chunk(item.get("metadata") or {}, item.get("text") or "", 0)["chunk_id"]
            for item in payloads
        }
        scope = {"org_id": organization_id, "document_id": document_id}

        async def orphan_point_ids() -> List[str]:
            # Delete by POINT id: a point another writer stored (LangChain uses
            # uuid5 of the chunk) has a point id that is not its chunk id, and
            # deleting by the chunk id would silently delete nothing.
            if list_points is not None:
                # A point is kept only when its POINT id is one this generation
                # wrote (``VectorClient`` uses the chunk id as the point id). A
                # foreign-shaped duplicate whose payload names a kept chunk is
                # still a stale, untagged vector and is removed.
                return [
                    str(point["id"])
                    for point in await list_points(
                        scope, namespace=EVIDENCE_VECTOR_NAMESPACE, limit=100000
                    )
                    if str(point["id"]) not in keep
                ]
            assert list_ids is not None  # guarded above: one lister exists
            return [
                str(point)
                for point in await list_ids(
                    scope, namespace=EVIDENCE_VECTOR_NAMESPACE, limit=100000
                )
                if str(point) not in keep
            ]

        async def still_the_owner() -> None:
            if heartbeat is not None:
                await heartbeat()
            if source_key:
                from .contract_source_lease import source_state

                source = await source_state(self._db, source_key)
                if not source.projectable or source.generation != source_generation:
                    raise ProjectionUnavailable(
                        f"the source of {document_id} changed during the build; its "
                        "superseded points are left to the build of the new source"
                    )

        try:
            orphans = await orphan_point_ids()
        except Exception as exc:
            raise ProjectionUnavailable(f"could not list superseded vector points: {exc}") from exc
        if not orphans:
            return
        # Outside the wrapper: a lost claim or a moved revision propagates as
        # itself (the generation is discarded), not as a build failure.
        await still_the_owner()
        try:
            await delete(orphans, namespace=EVIDENCE_VECTOR_NAMESPACE)
            # VectorClient.delete logs and swallows a store error, so the
            # outcome is proven by listing again rather than trusted.
            remaining = set(await orphan_point_ids())
            survivors = [point for point in orphans if point in remaining]
            if survivors:
                raise ProjectionUnavailable(
                    f"{len(survivors)} superseded vector point(s) of {document_id} "
                    "could not be deleted"
                )
        except ProjectionUnavailable:
            raise
        except Exception as exc:
            raise ProjectionUnavailable(f"could not remove superseded vector points: {exc}") from exc

    def _sync_graph(
        self,
        payloads: List[Dict[str, Any]],
        *,
        document_id: str,
        filename: str,
        organization_id: str,
        project_id: Optional[str],
    ) -> str:
        """Same graph write as upload ingestion, so no second node generation appears.

        Best effort by contract: the evidence route reports the graph source as
        degraded when it cannot answer, and legal promotion never depends on it.
        The outcome is returned and logged by the caller; it does not gate
        CURRENT. Runs in a worker thread because the Falkor client is blocking.
        """
        from .contract_graph_service import ContractGraphIdentityError, DocumentGraphPayload

        graph = self.graph
        if not getattr(graph, "enabled", False):
            return "disabled"
        ingestor = self.ingestor
        try:
            section_type, priority = ingestor._detect_section_type(filename)
            nodes = ingestor._build_clause_graph_nodes(payloads, section_type, priority)
            if not nodes:
                return "skipped: no clause nodes"
            graph.upsert_contract_graph(
                DocumentGraphPayload(
                    doc_id=document_id,
                    title=filename,
                    version=None,
                    organization_id=organization_id,
                    project_id=project_id,
                    section_type=section_type,
                    priority=priority,
                ),
                nodes,
            )
            return "synced"
        except ContractGraphIdentityError as exc:
            return f"not_representable: {exc}"
        except Exception as exc:
            logger.warning("reprojection graph sync failed for %s: %s", document_id, exc)
            return f"graph_error: {type(exc).__name__}"
