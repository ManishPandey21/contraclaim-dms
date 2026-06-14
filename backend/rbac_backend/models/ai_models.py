from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class LetterSearchRequest(BaseModel):
    """Request payload for semantic letter search."""

    query: str = Field(..., max_length=1000)
    limit: int = Field(default=5, ge=1, le=50)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class SimilarLetter(BaseModel):
    """Search result entry."""

    letter_id: str
    score: float
    subject: Optional[str] = None
    snippet: Optional[str] = None
    created_at: Optional[datetime] = None


class VectorSearchResponse(BaseModel):
    """Response for vector search queries."""

    results: List[SimilarLetter] = Field(default_factory=list)
    cached_results: bool = Field(default=False)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class LetterDraftRequest(BaseModel):
    """AI draft generation request."""

    subject: str = Field(..., max_length=500)
    recipient: str = Field(..., max_length=500)
    context: Optional[str] = Field(default=None, max_length=4000)
    points: Optional[str] = Field(default=None, max_length=4000)
    user_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    document_ids: List[str] = Field(default_factory=list)
    use_vector_store: bool = Field(default=True)
    letter_id: Optional[str] = None


class LetterDraftResponse(BaseModel):
    """AI-generated letter draft."""

    subject: str
    body: str
    key_points: List[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class StyleProfileResponse(BaseModel):
    """User-specific AI style profile."""

    profile_id: str
    tone: Optional[str] = None
    voice: Optional[str] = None
    examples: List[str] = Field(default_factory=list)
    updated_at: Optional[datetime] = None


class AIAssistantStats(BaseModel):
    """Aggregated AI assistant usage statistics."""

    requests_last_24h: int = 0
    cached_hits_last_24h: int = 0
    average_latency_ms: float = 0.0
    success_rate: float = 1.0
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class LangGraphNodeTrace(BaseModel):
    """Trace payload returned to the client for each node execution."""

    name: str
    status: str
    started_at: datetime
    completed_at: datetime
    data: dict = Field(default_factory=dict)


class LangGraphDraftRequest(LetterDraftRequest):
    """Request model for LangGraph drafting."""

    letter_id: str = Field(..., description="Target letter identifier")
    analysis_only: bool = Field(
        default=False,
        description="When true, stop after generating background summary/plan",
    )
    plan_override: Optional[str] = Field(
        default=None, description="User-provided plan text to override model output"
    )
    include_letter_codes: List[str] = Field(
        default_factory=list,
        description="Letter codes to include from Falkor graph/thread",
    )
    exclude_letter_codes: List[str] = Field(
        default_factory=list,
        description="Letter codes to exclude from Falkor graph/thread",
    )


class LangGraphDraftResponse(BaseModel):
    """Response payload emitted by LangGraph drafting endpoint."""

    letter_id: str
    run_id: str
    status: str
    plan: str
    draft: LetterDraftResponse
    warnings: List[str] = Field(default_factory=list)
    trace: List[LangGraphNodeTrace] = Field(default_factory=list)
    summary_points: List[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
    context_document_ids: List[str] = Field(default_factory=list)
    context_documents: List[dict] = Field(default_factory=list)
    background_summary: List[dict] = Field(default_factory=list)
    graph_thread: List[dict] = Field(default_factory=list)
    sources: List["DraftSource"] = Field(default_factory=list)
    reviewer_findings: List["DraftReviewFinding"] = Field(default_factory=list)
    reviewer_blocking: bool = Field(
        default=False,
        description="True when reviewer findings contain blocking errors",
    )
    tone_approach: Optional["StrategyToneApproach"] = None
    content_structure: Optional["StrategyContentStructure"] = None
    specific_responses: List["StrategyPointResponse"] = Field(default_factory=list)
    risk_mitigation: Optional["StrategyRiskMitigation"] = None
    desired_outcome: Optional["StrategyDesiredOutcome"] = None
    requirements_text: Optional[str] = None
    context_text: Optional[str] = None


class LangGraphLLMConfig(BaseModel):
    """Configurable LangGraph drafting/review LLM settings."""

    drafter_model: str
    reviewer_model: str
    plan_model: str
    draft_prompt_template: str
    plan_prompt_template: Optional[str] = None
    available_models: List[str] = Field(default_factory=list)
    updated_at: Optional[datetime] = None


class DraftSource(BaseModel):
    """Source material used by the drafting pipeline."""

    id: str
    source_type: Literal["contract_clause", "letter", "context_document", "comment", "other"]
    label: str
    snippet: Optional[str] = None
    document_id: Optional[str] = None
    letter_id: Optional[str] = None
    clause_number: Optional[str] = None
    clause_title: Optional[str] = None
    page_numbers: List[int] = Field(default_factory=list)
    score: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DraftReviewFinding(BaseModel):
    """Reviewer output for draft validation."""

    level: Literal["warning", "error"]
    message: str
    evidence: Optional[str] = None


class StrategyToneApproach(BaseModel):
    overall_tone: Optional[str] = None
    key_messaging_strategy: Optional[str] = None
    relationship_management_approach: Optional[str] = None


class StrategyContentStructure(BaseModel):
    opening_strategy: Optional[str] = None
    key_points_order: List[str] = Field(default_factory=list)
    contractual_references: List[str] = Field(default_factory=list)
    closing_approach: Optional[str] = None


class StrategyPointResponse(BaseModel):
    contractor_point: Optional[str] = None
    response_strategy: Optional[str] = None
    evidence_references: List[str] = Field(default_factory=list)
    contractual_basis: Optional[str] = None


class StrategyRiskMitigation(BaseModel):
    legal_risks: List[str] = Field(default_factory=list)
    relationship_risks: List[str] = Field(default_factory=list)
    project_impact_considerations: List[str] = Field(default_factory=list)


class StrategyDesiredOutcome(BaseModel):
    immediate_action: Optional[str] = None
    next_steps: List[str] = Field(default_factory=list)
    fallback_positions: List[str] = Field(default_factory=list)


class StrategyPlanRequest(BaseModel):
    letter_id: str
    role: Literal["contractor", "engineer", "employer"]
    audience: Optional[str] = Field(
        default=None, description="Recipient focus (used when engineer role is selected)"
    )
    subject: str
    recipient: str
    context: Optional[str] = None
    contractor_context: Optional[str] = None
    engineer_context: Optional[str] = None
    employer_context: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    summary_points: List[str] = Field(default_factory=list)
    document_ids: List[str] = Field(default_factory=list)
    points: Optional[str] = None
    requirements: Optional[str] = None


class StrategyPlanResponse(BaseModel):
    letter_id: str
    run_id: str
    status: str
    generated_at: datetime
    plan: str
    tone_approach: StrategyToneApproach = Field(default_factory=StrategyToneApproach)
    content_structure: StrategyContentStructure = Field(default_factory=StrategyContentStructure)
    specific_responses: List[StrategyPointResponse] = Field(default_factory=list)
    risk_mitigation: StrategyRiskMitigation = Field(default_factory=StrategyRiskMitigation)
    desired_outcome: StrategyDesiredOutcome = Field(default_factory=StrategyDesiredOutcome)
    summary_points: List[str] = Field(default_factory=list)
    background_summary: List[dict] = Field(default_factory=list)
    context_document_ids: List[str] = Field(default_factory=list)
    context_documents: List[dict] = Field(default_factory=list)
    trace: List[LangGraphNodeTrace] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class StrategyContextRequest(BaseModel):
    refresh: bool = Field(
        default=True,
        description="When true, recompute consolidated contexts even if cached values exist",
    )
    include_documents: bool = Field(
        default=True, description="Include linked documents when synthesising background summary"
    )


class StrategyContextTimelineEntry(BaseModel):
    letter_id: str
    role: str
    letter_no: Optional[str] = None
    subject: Optional[str] = None
    date: Optional[str] = None


class StrategyContextResponse(BaseModel):
    letter_id: str
    contractor_context: Optional[str] = None
    engineer_context: Optional[str] = None
    employer_context: Optional[str] = None
    thread_letters: List[str] = Field(default_factory=list)
    timeline: List[StrategyContextTimelineEntry] = Field(default_factory=list)


LangGraphDraftResponse.model_rebuild()
