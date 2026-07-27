from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils.datetime import isoformat_z, now_utc


class LetterReference(BaseModel):
    id: str
    title: str
    subject: str
    date: str
    reference_number: str


class ConversationReference(BaseModel):
    type: str = Field(..., pattern="^(internal|external)$")
    letter_id: Optional[str] = None
    letter_no: Optional[str] = None
    note: Optional[str] = None

    @field_validator("letter_id")
    @classmethod
    def _validate_internal(cls, value: Optional[str], info) -> Optional[str]:
        if info.data.get("type") == "internal" and not value:
            raise ValueError("letter_id is required for internal references")
        return value

    @field_validator("letter_no")
    @classmethod
    def _validate_external(cls, value: Optional[str], info) -> Optional[str]:
        if info.data.get("type") == "external" and not value:
            raise ValueError("letter_no is required for external references")
        return value


class LetterStatusEvent(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        extra="ignore",
    )

    status: str
    changed_at: datetime = Field(default_factory=now_utc, alias="changed_at")
    actor_id: Optional[str] = Field(default=None, alias="actor_id")
    comment: Optional[str] = None


class LetterComment(BaseModel):
    """Comment entry with optional metadata."""

    text: str
    timestamp: Optional[datetime] = None
    user_id: Optional[str] = None
    type: Optional[str] = None


class DraftVersion(BaseModel):
    """Immutable snapshot of each LangGraph draft run."""

    version: int
    status: str
    body: str
    plan: Optional[str] = None
    sources: List[dict] = Field(default_factory=list)
    reviewer_findings: List[dict] = Field(default_factory=list)
    run_id: Optional[str] = None
    locked: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now_utc)
    created_by: Optional[str] = None


class StrategyVersion(BaseModel):
    """Immutable snapshot of each generated or accepted strategic plan."""

    version: int
    status: str
    plan: str
    planning_sheet: Optional[dict] = None
    reply_matrix: List[dict] = Field(default_factory=list)
    incoming_analysis: Optional[dict] = None
    source_ids: List[str] = Field(default_factory=list)
    run_id: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    created_by: Optional[str] = None


class Letter(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        extra="ignore",
        json_encoders={
            ObjectId: str,
            datetime: isoformat_z,
        },
    )

    id: Optional[str] = Field(None, alias="_id")
    title: str
    recipient: str
    subject: str
    content: str
    status: str = "Draft"
    created_by: str
    assigned_to: str
    organization_id: str
    project_id: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    comments: Optional[List[LetterComment]] = Field(default_factory=list)
    reference: Optional[LetterReference] = None
    embedding: Optional[List[float]] = None
    status_history: List[LetterStatusEvent] = Field(default_factory=list, alias="status_history")
    status_start_date: Optional[datetime] = Field(default=None, alias="statusStartDate")
    pendency_days: Optional[int] = Field(default=None, alias="pendencyDays")
    letter_no: Optional[str] = Field(None, description="Unique letter number per project")
    date: Optional[datetime] = Field(default_factory=now_utc, description="Letter date")
    from_party_id: Optional[str] = None
    to_party_id: Optional[str] = None
    conversation_id: Optional[str] = None
    previous_letter_id: Optional[str] = None
    references: List[ConversationReference] = Field(default_factory=list)
    ancestors: List[str] = Field(default_factory=list)
    depth: int = Field(default=0)
    strategy_plan: Optional[str] = Field(
        default=None, description="Strategic plan captured during the Strategy stage"
    )
    strategic_outline: Optional[dict] = Field(
        default=None, description="Structured AI-generated plan sections"
    )
    summary_points: List[str] = Field(default_factory=list)
    strategy_plan_approved_by: Optional[str] = Field(default=None)
    strategy_plan_approved_at: Optional[datetime] = Field(default=None)
    strategy_versions: List[StrategyVersion] = Field(default_factory=list)
    current_strategy_version: Optional[int] = None
    accepted_strategy_version: Optional[int] = None
    strategy_role: Optional[str] = Field(
        default=None,
        description="Perspective selected for the current strategy plan (contractor/engineer/employer)",
    )
    strategy_recipient: Optional[str] = Field(
        default=None, description="Recipient focus when engineer role is selected"
    )
    strategy_run_id: Optional[str] = Field(default=None)
    strategy_graph_status: Optional[str] = Field(default=None)
    strategy_graph_trace: List[dict] = Field(default_factory=list)
    strategy_graph_started_at: Optional[datetime] = Field(default=None)
    strategy_graph_completed_at: Optional[datetime] = Field(default=None)
    strategy_started_at: Optional[datetime] = Field(default=None)
    strategy_completed_at: Optional[datetime] = Field(default=None)
    duration_strategy: Optional[float] = Field(default=None, description="Hours spent in strategy stage")
    contractor_context: Optional[str] = Field(default=None, description="Consolidated contractor perspective")
    engineer_context: Optional[str] = Field(default=None, description="Consolidated engineer perspective")
    employer_context: Optional[str] = Field(default=None, description="Consolidated employer perspective")
    thread_id: Optional[str] = Field(default=None, description="Conversation/thread identifier")
    thread_letters: List[str] = Field(default_factory=list, description="Letter ids in the current thread")
    correspondence_type: Optional[str] = Field(default=None)
    parties_involved: List[str] = Field(default_factory=list)
    draft_plan: Optional[str] = Field(default=None)
    draft_output: Optional[str] = Field(default=None)
    drafting_profile: Optional[str] = Field(
        default=None,
        description="Assigned drafting profile: contractor, engineer_representation, or employer_contract_review",
    )
    drafting_assigned_by: Optional[str] = Field(default=None)
    drafting_assigned_at: Optional[datetime] = Field(default=None)
    draft_trace: List[dict] = Field(default_factory=list)
    graph_status: Optional[str] = Field(default=None)
    graph_started_at: Optional[datetime] = Field(default=None)
    graph_completed_at: Optional[datetime] = Field(default=None)
    graph_run_id: Optional[str] = Field(default=None)
    graph_warnings: List[str] = Field(default_factory=list)
    context_document_ids: List[str] = Field(default_factory=list)
    context_documents: List[dict] = Field(default_factory=list)
    background_summary: List[dict] = Field(default_factory=list)
    background_annotations: Optional[str] = Field(default=None)
    outline_last_edited_by: Optional[str] = Field(default=None)
    outline_last_edited_at: Optional[datetime] = Field(default=None)
    graph_thread: List[dict] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    draft_sources: List[dict] = Field(default_factory=list)
    reviewer_findings: List[dict] = Field(default_factory=list)
    reviewer_blocking: Optional[bool] = None
    draft_versions: List[DraftVersion] = Field(default_factory=list)
    current_draft_version: Optional[int] = None
    approved_draft_version: Optional[int] = None
    approved_run_id: Optional[str] = None
    governed_draft_hash: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    approved_version_locked: bool = False

    @field_validator("comments", mode="before")
    @classmethod
    def _coerce_comments(cls, value):
        """
        Accept legacy string comments or dicts with text; normalize to LetterComment list.
        """
        if value is None:
            return []
        if not isinstance(value, list):
            return []
        normalized = []
        for item in value:
            if isinstance(item, str):
                normalized.append({"text": item})
            elif isinstance(item, dict):
                # ensure text exists for validator; otherwise skip
                if "text" in item and isinstance(item["text"], str):
                    normalized.append(item)
                else:
                    continue
            else:
                continue
        return normalized


class LetterCreate(BaseModel):
    title: str
    recipient: str
    subject: str
    content: Optional[str] = ""
    assigned_to: str
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    reference: Optional[LetterReference] = None
    letter_no: Optional[str] = None
    date: Optional[datetime] = None
    from_party_id: Optional[str] = None
    to_party_id: Optional[str] = None
    previous_letter_id: Optional[str] = None
    references: Optional[List[ConversationReference]] = None
    strategy_role: Optional[str] = None
    strategy_recipient: Optional[str] = None
    contractor_context: Optional[str] = None
    engineer_context: Optional[str] = None
    employer_context: Optional[str] = None


class LetterUpdate(BaseModel):
    title: Optional[str] = None
    recipient: Optional[str] = None
    subject: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    comments: Optional[List[LetterComment]] = None
    reference: Optional[LetterReference] = None
    letter_no: Optional[str] = None
    date: Optional[datetime] = None
    from_party_id: Optional[str] = None
    to_party_id: Optional[str] = None
    references: Optional[List[ConversationReference]] = None
    strategy_plan: Optional[str] = None
    strategic_outline: Optional[dict] = None
    strategy_role: Optional[str] = None
    strategy_recipient: Optional[str] = None
    strategy_plan_approved_by: Optional[str] = None
    strategy_plan_approved_at: Optional[datetime] = None
    strategy_versions: Optional[List[StrategyVersion]] = None
    current_strategy_version: Optional[int] = None
    accepted_strategy_version: Optional[int] = None
    strategy_run_id: Optional[str] = None
    strategy_graph_status: Optional[str] = None
    strategy_graph_trace: Optional[List[dict]] = None
    strategy_graph_started_at: Optional[datetime] = None
    strategy_graph_completed_at: Optional[datetime] = None
    strategy_started_at: Optional[datetime] = None
    strategy_completed_at: Optional[datetime] = None
    duration_strategy: Optional[float] = None
    contractor_context: Optional[str] = None
    engineer_context: Optional[str] = None
    employer_context: Optional[str] = None
    thread_id: Optional[str] = None
    thread_letters: Optional[List[str]] = None
    correspondence_type: Optional[str] = None
    parties_involved: Optional[List[str]] = None
    draft_plan: Optional[str] = None
    draft_output: Optional[str] = None
    drafting_profile: Optional[str] = None
    drafting_assigned_by: Optional[str] = None
    drafting_assigned_at: Optional[datetime] = None
    draft_trace: Optional[List[dict]] = None
    graph_status: Optional[str] = None
    graph_started_at: Optional[datetime] = None
    graph_completed_at: Optional[datetime] = None
    graph_run_id: Optional[str] = None
    graph_warnings: Optional[List[str]] = None
    summary_points: Optional[List[str]] = None
    context_document_ids: Optional[List[str]] = None
    context_documents: Optional[List[dict]] = None
    background_summary: Optional[List[dict]] = None
    background_annotations: Optional[str] = None
    outline_last_edited_by: Optional[str] = None
    outline_last_edited_at: Optional[datetime] = None
    graph_thread: Optional[List[dict]] = None
    draft_sources: Optional[List[dict]] = None
    reviewer_findings: Optional[List[dict]] = None
    reviewer_blocking: Optional[bool] = None
    draft_versions: Optional[List[DraftVersion]] = None
    current_draft_version: Optional[int] = None
    approved_draft_version: Optional[int] = None
    approved_run_id: Optional[str] = None
    governed_draft_hash: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    approved_version_locked: Optional[bool] = None


class ConversationTree(BaseModel):
    letter: Letter
    children: List["ConversationTree"] = Field(default_factory=list)


class ConversationSummary(BaseModel):
    conversation_id: str
    root_letter: Letter
    total_letters: int
    latest_letter: Letter
    participants: List[str]
    created_at: datetime
    updated_at: datetime


ConversationTree.model_rebuild()
