from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..retrieval.models import Citation, SearchFilters, SearchStrategy


class AgentMessage(BaseModel):
    role: str
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    citations: List[Citation] = Field(default_factory=list)
    tool_traces: List[Dict[str, Any]] = Field(default_factory=list)


class AgentConversation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="conversation_id")
    org_id: str
    project_id: str
    participants: List[str] = Field(default_factory=list)
    messages: List[AgentMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True)


class AgentRequest(BaseModel):
    conversation_id: Optional[str] = None
    org_id: str
    project_id: str
    incoming_letter_id: Optional[str] = None
    incoming_text: Optional[str] = None
    user_goal: Optional[str] = None
    strategy: SearchStrategy = SearchStrategy.VANILLA
    filters: Optional[SearchFilters] = None
    streaming: bool = False


class AgentResponse(BaseModel):
    conversation_id: str
    questions_to_user: Optional[List[str]] = None
    issues_to_address: List[str] = Field(default_factory=list)
    draft_reply: str
    citations: List[Citation]
    tool_traces: List[Dict[str, Any]]
    timings: Dict[str, float] = Field(default_factory=dict)
