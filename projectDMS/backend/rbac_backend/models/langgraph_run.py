from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LangGraphNodeTrace(BaseModel):
    """Represents a single node execution trace."""

    name: str
    status: str
    started_at: datetime
    completed_at: datetime
    data: Dict[str, Any] = Field(default_factory=dict)


class LangGraphRun(BaseModel):
    """Stored LangGraph run metadata for a letter."""

    letter_id: str
    run_id: str
    status: str
    plan: Optional[str] = None
    draft_body: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    trace: List[LangGraphNodeTrace] = Field(default_factory=list)
    summary_points: List[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
