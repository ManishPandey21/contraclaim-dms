from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, BaseSettings, Field
from tenacity import retry, stop_after_attempt, wait_exponential

try:  # Optional OpenAI support
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover - optional dependency
    AsyncOpenAI = None  # type: ignore

try:  # graphiti-core FalkorDB client (optional)
    from graphiti_core.clients.falkordb import FalkorDBClient  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    FalkorDBClient = None  # type: ignore

from redis.commands.graph import Graph
from redis.commands.graph.node import Node
from redis.commands.graph.edge import Edge


class Settings(BaseSettings):
    graph_name: str = Field(default_factory=lambda: Path("contract_graph").stem)
    falkordb_url: str = Field("redis://falkordb:6379", alias="GRAPHITI_DB_URL")
    openai_api_key: Optional[str] = Field(None, alias="GRAPHITI_OPENAI_API_KEY")
    openai_model: str = Field("gpt-4o-mini")
    log_level: str = Field("INFO", alias="GRAPHITI_LOG_LEVEL")
    log_dir: Path = Field(Path("/app/logs"))
    default_limit: int = Field(25, ge=1, le=500)

    class Config:
        env_file = ".env"
        env_prefix = "GRAPHITI_"
        case_sensitive = False


settings = Settings()
settings.log_dir.mkdir(parents=True, exist_ok=True)

logger.remove()
logger.add(settings.log_dir / "graphiti.log", level=settings.log_level, rotation="10 MB", retention="10 files")
logger.add(lambda msg: print(msg, end=""), level=settings.log_level)

app = FastAPI(title="Graphiti Knowledge Graph API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class GraphDocument(BaseModel):
    id: str = Field(..., description="Unique document identifier")
    title: str
    body: str
    tags: List[str] = Field(default_factory=list)
    source: Optional[str] = None
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    generate_summary: bool = Field(False, description="Use OpenAI to auto-summarise body")


class GraphEpisode(BaseModel):
    id: str
    title: str
    description: str
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    related_documents: List[str] = Field(default_factory=list)


class RelationshipPayload(BaseModel):
    source_id: str
    target_id: str
    relationship: str = Field(..., description="Relationship type label, e.g. REFERENCES")
    properties: Dict[str, Any] = Field(default_factory=dict)


class TemporalQuery(BaseModel):
    start: datetime = Field(..., description="Start of temporal window")
    end: datetime = Field(..., description="End of temporal window")
    label: str = Field("Document", description="Node label to scope temporal search")
    limit: int = Field(50, ge=1, le=500)


class QueryRequest(BaseModel):
    query: str = Field(..., description="Raw Cypher query to execute")
    parameters: Dict[str, Any] = Field(default_factory=dict)


class QueryResponse(BaseModel):
    columns: List[str]
    rows: List[Dict[str, Any]]
    statistics: Dict[str, Any]


class GraphitiService:
    def __init__(self, graph_name: str, url: str) -> None:
        self.redis = redis.from_url(url)
        self.graph = Graph(graph_name, self.redis)
        self.graph_name = graph_name
        self._graphiti_client = self._init_graphiti_client(url, graph_name)

    @staticmethod
    def _init_graphiti_client(url: str, graph_name: str) -> Optional[Any]:
        if FalkorDBClient is None:
            return None
        try:
            return FalkorDBClient(url=url, graph_name=graph_name)
        except Exception as exc:  # pragma: no cover - optional
            logger.warning("Falling back to native RedisGraph client: {}", exc)
            return None

    def _run(self, query: str, params: Optional[Dict[str, Any]] = None) -> QueryResponse:
        logger.debug("Executing graph query: %s", query)
        result = self.graph.query(query, params or {})
        columns = [col for col in result.header]
        rows = [self._row_to_dict(columns, row) for row in result.result_set]
        stats = {metric: value for metric, value in result.statistics.items()}
        return QueryResponse(columns=columns, rows=rows, statistics=stats)

    def _row_to_dict(self, columns: List[str], row: List[Any]) -> Dict[str, Any]:
        mapped: Dict[str, Any] = {}
        for idx, value in enumerate(row):
            mapped[columns[idx]] = self._coerce(value)
        return mapped

    def _coerce(self, value: Any) -> Any:
        if isinstance(value, Node):
            return {
                "id": value.id,
                "labels": value.label,
                "properties": value.properties,
            }
        if isinstance(value, Edge):
            return {
                "id": value.id,
                "relationship": value.relation,
                "properties": value.properties,
                "source": value.src_node,
                "target": value.dest_node,
            }
        if isinstance(value, list):
            return [self._coerce(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def health(self) -> bool:
        return bool(self.redis.ping())

    def upsert_document(self, document: GraphDocument, summary: Optional[str] = None) -> QueryResponse:
        params = {
            "doc_id": document.id,
            "title": document.title,
            "body": document.body,
            "tags": document.tags,
            "source": document.source,
            "occurred_at": document.occurred_at.isoformat(),
            "metadata": document.metadata,
            "summary": summary,
        }
        query = (
            "MERGE (d:Document {id: $doc_id}) "
            "SET d.title = $title, "
            "    d.body = $body, "
            "    d.tags = $tags, "
            "    d.source = $source, "
            "    d.occurred_at = $occurred_at, "
            "    d.metadata = $metadata, "
            "    d.summary = COALESCE($summary, d.summary), "
            "    d.updated_at = timestamp() "
            "RETURN d"
        )
        return self._run(query, params)

    def add_episode(self, episode: GraphEpisode) -> QueryResponse:
        params = {
            "episode_id": episode.id,
            "title": episode.title,
            "description": episode.description,
            "occurred_at": episode.occurred_at.isoformat(),
            "metadata": episode.metadata,
            "doc_ids": episode.related_documents,
        }
        query = (
            "MERGE (e:Episode {id: $episode_id}) "
            "SET e.title = $title, "
            "    e.description = $description, "
            "    e.occurred_at = $occurred_at, "
            "    e.metadata = $metadata, "
            "    e.updated_at = timestamp() "
            "WITH e, $doc_ids AS docs "
            "FOREACH(docId IN docs | "
            "  MERGE (d:Document {id: docId}) "
            "  MERGE (e)-[:RELATES_TO]->(d) "
            ") "
            "RETURN e"
        )
        return self._run(query, params)

    def search(self, term: str, limit: int) -> QueryResponse:
        params = {
            "term": term.lower(),
            "limit": limit,
        }
        query = (
            "MATCH (d:Document) "
            "WHERE toLower(d.title) CONTAINS $term OR toLower(d.body) CONTAINS $term "
            "RETURN d ORDER BY d.occurred_at DESC LIMIT $limit"
        )
        return self._run(query, params)

    def relate(self, payload: RelationshipPayload) -> QueryResponse:
        params = {
            "source": payload.source_id,
            "target": payload.target_id,
            "properties": payload.properties,
        }
        query = (
            "MATCH (a {id: $source}), (b {id: $target}) "
            f"MERGE (a)-[r:{payload.relationship}]->(b) "
            "SET r += $properties, r.updated_at = timestamp() "
            "RETURN r"
        )
        return self._run(query, params)

    def temporal(self, request: TemporalQuery) -> QueryResponse:
        params = {
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "limit": request.limit,
        }
        query = (
            f"MATCH (n:{request.label}) "
            "WHERE n.occurred_at >= $start AND n.occurred_at <= $end "
            "RETURN n ORDER BY n.occurred_at ASC LIMIT $limit"
        )
        return self._run(query, params)

    def raw(self, request: QueryRequest) -> QueryResponse:
        return self._run(request.query, request.parameters)


graphiti_service = GraphitiService(settings.graph_name, settings.falkordb_url)
openai_client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key and AsyncOpenAI else None


@retry(wait=wait_exponential(multiplier=1, min=1, max=5), stop=stop_after_attempt(3))
async def generate_summary(document: GraphDocument) -> Optional[str]:
    if openai_client is None:
        return None
    prompt = (
        "Summarise the following contract document in under 120 words, highlight key obligations.\n\n"
        f"TITLE: {document.title}\n"
        f"BODY: {document.body}"
    )
    response = await openai_client.responses.create(
        model=settings.openai_model,
        input=prompt,
        max_output_tokens=200,
    )
    text = getattr(response, "output_text", None)
    if text:
        return text.strip()
    chunks = getattr(response, "output", None)
    if not chunks:
        return None
    assembled = " ".join(getattr(chunk, "text", "").strip() for chunk in chunks if getattr(chunk, "text", ""))
    return assembled.strip() or None


@app.on_event("startup")
async def startup_event() -> None:
    logger.info("Graphiti service starting with graph '%s'", settings.graph_name)
    try:
        if not graphiti_service.health():  # pragma: no cover - always true when redis ok
            raise RuntimeError("FalkorDB ping failed")
    except redis.RedisError as exc:
        logger.exception("Cannot connect to FalkorDB: %s", exc)
        raise
    if openai_client:
        logger.info("OpenAI integration enabled using model %s", settings.openai_model)
    else:
        logger.info("OpenAI integration disabled")


@app.get("/health", tags=["health"])
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness() -> Dict[str, str]:
    try:
        graphiti_service.health()
    except redis.RedisError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return {"status": "ready"}


@app.post("/documents", response_model=QueryResponse, tags=["documents"])
async def upsert_document(document: GraphDocument) -> QueryResponse:
    summary: Optional[str] = None
    if document.generate_summary:
        summary = await generate_summary(document)
    try:
        return graphiti_service.upsert_document(document, summary)
    except redis.RedisError as exc:
        logger.exception("Failed to upsert document")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/episodes", response_model=QueryResponse, tags=["episodes"])
async def add_episode(episode: GraphEpisode) -> QueryResponse:
    try:
        return graphiti_service.add_episode(episode)
    except redis.RedisError as exc:
        logger.exception("Failed to add episode")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/search", response_model=QueryResponse, tags=["search"])
async def search(term: str = Query(..., min_length=2), limit: int = Query(settings.default_limit, ge=1, le=500)) -> QueryResponse:
    try:
        return graphiti_service.search(term, limit)
    except redis.RedisError as exc:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/relationships", response_model=QueryResponse, tags=["relationships"])
async def relate(payload: RelationshipPayload) -> QueryResponse:
    try:
        return graphiti_service.relate(payload)
    except redis.RedisError as exc:
        logger.exception("Relationship creation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/temporal", response_model=QueryResponse, tags=["analytics"])
async def temporal(query: TemporalQuery) -> QueryResponse:
    try:
        return graphiti_service.temporal(query)
    except redis.RedisError as exc:
        logger.exception("Temporal query failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/graph/query", response_model=QueryResponse, tags=["admin"])
async def raw_query(request: QueryRequest) -> QueryResponse:
    try:
        return graphiti_service.raw(request)
    except redis.RedisError as exc:
        logger.exception("Raw query failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


