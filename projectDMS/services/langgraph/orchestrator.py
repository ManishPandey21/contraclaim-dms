from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import redis
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

try:
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import MemorySaver
except Exception as exc:  # pragma: no cover - required dependency
    raise RuntimeError("langgraph must be installed to run this service") from exc


class Settings(BaseSettings):
    redis_url: str = Field("redis://redis:6379/0", alias="LANGGRAPH_REDIS_URL")
    graphiti_url: str = Field("http://graphiti:8080", alias="LANGGRAPH_GRAPHITI_URL")
    qdrant_url: str = Field("http://qdrant:6333", alias="LANGGRAPH_QDRANT_URL")
    qdrant_api_key: str = Field("", alias="LANGGRAPH_QDRANT_API_KEY")
    backend_url: str = Field("http://backend:8000", alias="LANGGRAPH_BACKEND_URL")
    api_token: str = Field("", alias="LANGGRAPH_API_TOKEN")
    log_dir: Path = Field(Path("/app/logs"), alias="LANGGRAPH_LOG_DIR")
    workflow_queue: str = Field("workflow:ingest", alias="LANGGRAPH_INGEST_QUEUE")
    ws_keepalive_interval: int = Field(15, alias="LANGGRAPH_WS_KEEPALIVE")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
settings.log_dir.mkdir(parents=True, exist_ok=True)

logger.remove()
logger.add(settings.log_dir / "langgraph.log", rotation="10 MB", retention="15 files")
logger.add(lambda msg: print(msg, end=""))

app = FastAPI(title="LangGraph Contract Orchestrator", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_client: redis.Redis = redis.from_url(settings.redis_url, decode_responses=True)
websocket_clients: Dict[str, WebSocket] = {}
ws_lock = asyncio.Lock()


class ContractState(BaseModel):
    job_id: str
    document_ids: List[str] = Field(default_factory=list)
    status: str = "pending"
    events: List[Dict[str, Any]] = Field(default_factory=list)
    analysis: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    report_url: Optional[str] = None

    def add_event(self, stage: str, data: Optional[Dict[str, Any]] = None) -> None:
        self.events.append({
            "stage": stage,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data or {},
        })


class WorkflowRequest(BaseModel):
    documents: List[str]
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowResponse(BaseModel):
    job_id: str
    status: str


class ContractGraph:
    def __init__(self) -> None:
        self.graph = StateGraph(ContractState)
        self.checkpointer = MemorySaver()
        self._build()

    def _build(self) -> None:
        self.graph.add_node("ingest", self.ingest_documents)
        self.graph.add_node("analyze", self.contract_analysis)
        self.graph.add_node("compliance", self.compliance_check)
        self.graph.add_node("risk", self.risk_assessment)
        self.graph.add_node("report", self.generate_report)

        self.graph.add_edge(START, "ingest")
        self.graph.add_edge("ingest", "analyze")
        self.graph.add_edge("analyze", "compliance")
        self.graph.add_edge("compliance", "risk")
        self.graph.add_edge("risk", "report")
        self.graph.add_edge("report", END)

        self.app = self.graph.compile(checkpointer=self.checkpointer)

    async def ingest_documents(self, state: ContractState) -> ContractState:
        state.add_event("ingest:start")
        await broadcast_state(state)
        async with httpx.AsyncClient(timeout=30.0) as client:
            for doc_id in state.document_ids:
                await self._call_backend_ingest(client, doc_id)
        state.add_event("ingest:complete")
        await broadcast_state(state)
        return state

    async def contract_analysis(self, state: ContractState) -> ContractState:
        state.add_event("analysis:start")
        await broadcast_state(state)
        state.analysis["summary"] = await self._call_graphiti(state.document_ids)
        state.add_event("analysis:complete", {"summary": state.analysis.get("summary")})
        await broadcast_state(state)
        return state

    async def compliance_check(self, state: ContractState) -> ContractState:
        state.add_event("compliance:start")
        await broadcast_state(state)
        state.analysis["compliance"] = {"status": "pending", "issues": []}
        try:
            state.analysis["compliance"] = await self._call_graphiti_rules("compliance", state.document_ids)
        except Exception as exc:
            logger.warning("Compliance check fallback: %s", exc)
        state.add_event("compliance:complete", state.analysis.get("compliance"))
        await broadcast_state(state)
        return state

    async def risk_assessment(self, state: ContractState) -> ContractState:
        state.add_event("risk:start")
        await broadcast_state(state)
        try:
            state.analysis["risk"] = await self._call_graphiti_rules("risk", state.document_ids)
        except Exception as exc:
            logger.warning("Risk assessment fallback: %s", exc)
            state.analysis["risk"] = {"level": "unknown", "issues": []}
        state.add_event("risk:complete", state.analysis.get("risk"))
        await broadcast_state(state)
        return state

    async def generate_report(self, state: ContractState) -> ContractState:
        state.add_event("report:start")
        await broadcast_state(state)
        # Placeholder: in real use a document generator would run here
        report_path = settings.log_dir / f"report_{state.job_id}.json"
        report_payload = {
            "job_id": state.job_id,
            "analysis": state.analysis,
            "generated_at": datetime.utcnow().isoformat(),
        }
        report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
        state.report_url = str(report_path)
        state.add_event("report:complete", {"report_url": state.report_url})
        await broadcast_state(state)
        state.status = "completed"
        return state

    @retry(wait=wait_exponential(multiplier=1, min=1, max=5), stop=stop_after_attempt(3), reraise=True)
    async def _call_backend_ingest(self, client: httpx.AsyncClient, doc_id: str) -> None:
        headers = {}
        if settings.api_token:
            headers["X-API-Token"] = settings.api_token
        url = f"{settings.backend_url}/api/internal/documents/{doc_id}/process"
        logger.info("Triggering backend processing for %s via %s", doc_id, url)
        response = await client.post(url, headers=headers or None)
        response.raise_for_status()

    async def _call_graphiti(self, docs: List[str]) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.graphiti_url}/graph/query",
                json={
                    "query": "MATCH (d:Document) WHERE d.id IN $ids RETURN d",
                    "parameters": {"ids": docs},
                },
            )
            response.raise_for_status()
            return response.json()

    async def _call_graphiti_rules(self, rule: str, docs: List[str]) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.graphiti_url}/graph/query",
                json={
                    "query": "MATCH (d:Document)-[:HAS_ISSUE]->(i:Issue {type: $rule}) WHERE d.id IN $ids RETURN d, i",
                    "parameters": {"ids": docs, "rule": rule},
                },
            )
            response.raise_for_status()
            return response.json()


contract_graph = ContractGraph()


async def require_token(x_api_token: str | None = Header(default=None)) -> None:
    if settings.api_token and x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="Invalid API token")


async def broadcast_state(state: ContractState) -> None:
    message = state.model_dump()
    async with ws_lock:
        stale = []
        for job_id, ws in websocket_clients.items():
            if ws.application_state.value != 2:  # State.DISCONNECTED
                try:
                    await ws.send_json({"type": "state", "payload": message})
                except Exception as exc:
                    logger.warning("WebSocket send failed: %s", exc)
                    stale.append(job_id)
        for job_id in stale:
            websocket_clients.pop(job_id, None)


@app.get("/health")
async def health() -> Dict[str, str]:
    try:
        redis_client.ping()
    except redis.RedisError as exc:
        logger.error("Redis ping failed: %s", exc)
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc
    return {"status": "ok"}


@app.post("/workflows", response_model=WorkflowResponse, dependencies=[Depends(require_token)])
async def start_workflow(request: WorkflowRequest) -> WorkflowResponse:
    if not request.documents:
        raise HTTPException(status_code=400, detail="documents cannot be empty")
    job_id = uuid.uuid4().hex
    state = ContractState(job_id=job_id, document_ids=request.documents, metadata=request.metadata)
    state.add_event("workflow:created")
    redis_client.hset("workflow:state", job_id, state.json())
    asyncio.create_task(run_workflow(state))
    return WorkflowResponse(job_id=job_id, status="queued")


async def run_workflow(state: ContractState) -> None:
    state.status = "processing"
    await broadcast_state(state)
    try:
        result = await contract_graph.app.ainvoke(state)
        redis_client.hset("workflow:state", state.job_id, result.json())
    except Exception as exc:
        logger.exception("Workflow %s failed", state.job_id)
        state.status = "failed"
        state.add_event("workflow:failed", {"error": str(exc)})
        redis_client.hset("workflow:state", state.job_id, state.json())
        await broadcast_state(state)


@app.get("/workflows/{job_id}", response_model=ContractState, dependencies=[Depends(require_token)])
async def get_workflow(job_id: str) -> ContractState:
    data = redis_client.hget("workflow:state", job_id)
    if not data:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return ContractState.model_validate_json(data)


@app.websocket("/ws/{job_id}")
async def websocket_endpoint(ws: WebSocket, job_id: str) -> None:
    await ws.accept()
    async with ws_lock:
        websocket_clients[job_id] = ws
    try:
        while True:
            await asyncio.sleep(settings.ws_keepalive_interval)
            await ws.send_json({"type": "keepalive", "timestamp": datetime.utcnow().isoformat()})
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for job %s", job_id)
    finally:
        async with ws_lock:
            websocket_clients.pop(job_id, None)


@app.get("/agents/collections", dependencies=[Depends(require_token)])
async def collections() -> Any:
    async with httpx.AsyncClient(timeout=5.0) as client:
        headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else None
        response = await client.get(f"{settings.qdrant_url}/collections", headers=headers)
        response.raise_for_status()
        return response.json()


@app.post("/agents/retry/{job_id}", dependencies=[Depends(require_token)])
async def retry_workflow(job_id: str) -> WorkflowResponse:
    data = redis_client.hget("workflow:state", job_id)
    if not data:
        raise HTTPException(status_code=404, detail="Workflow not found")
    state = ContractState.model_validate_json(data)
    state.add_event("workflow:retry")
    redis_client.hset("workflow:state", job_id, state.json())
    asyncio.create_task(run_workflow(state))
    return WorkflowResponse(job_id=job_id, status="retrying")
