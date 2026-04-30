from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..core.config import settings
from ..models.ai_models import LangGraphLLMConfig
from ..core.database import get_database

# Centralised defaults for LangGraph drafting configuration
DEFAULT_LLM_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "grok-4-1-fast",
    "grok-4",
    "gemini-2.5",
]

DEFAULT_DRAFT_PROMPT_TEMPLATE = """You are a Chief Contracts Expert advising the General Consultant (GC) on behalf of the Employer (Client) for a large-scale Indian infrastructure project (e.g., Metro, Railways, Highways).

Your task is to analyze and plan a reply to the letter/notice received from the Contractor. Ensure all observations are grounded in the Contract Agreement (CA), General Conditions of Contract (GCC), Special Conditions of Contract (SCC), and Indian law.

Subject: {subject}
Recipient: {recipient}

Plan:
{plan}

Requirements / context:
{requirements}

Sources (reference by [S#] in the body):
{sources}

Draft guidance:
- Keep the tone professional; avoid new commitments beyond cited facts.
- Cite evidence inline as [S1], [S2] where relevant.
- Preserve contractual posture; avoid speculative claims.
- Return only the letter body (no metadata or JSON)."""

DEFAULT_PLAN_PROMPT_TEMPLATE = """You are a senior legal counsel and contract management expert. Analyze the provided context and requirements to produce a comprehensive strategic plan for contract correspondence.

Subject: {subject}
Role: {role}
Recipient: {recipient}
Contexts:
- Contractor: {contractor_context}
- Engineer: {engineer_context}
- Employer: {employer_context}
Requirements: {requirements}
Graph-linked letters: {linked_letters}
Sources: {sources}

OUTPUT FORMAT - Follow this exact structure:

🗂️ Document Summary
Type: [Brief description of the correspondence type and scenario]
Date Received: [Date or N/A]
Reply Due By: [Deadline or N/A]

🚫 Contractual Flaws in Contractor's Submission
[List any contractual non-compliance, time-bar issues, or procedural flaws. If none, state "N/A"]

📘 Contractual Compliance & Validation
Notice Verification: [Verify compliance with notice requirements and procedures]
Summary & Fact Check: [Key facts and contract alignment assessment]
Entitlement Assessment: [Contractor's rights and entitlements analysis]

⚖️ Legal & Contractual Grounding
Contractual Clauses: [Cite precise clauses from contract documents]
Statutory References: [Relevant laws and regulations]
Legal Precedents: [Case law and judicial precedents]

📑 Legal & Contractual References
[Create a table with columns: Reference Type, Description, Clause/Section]

🛠️ GC's Strategic Response
Drafting Guidance: [Specific guidance for letter drafting]
Risk Mitigation: [Strategies to minimize legal/financial risks]
Engineer's Determination: [Recommended determinations or instructions]

✉️ Recommended Response Strategy
Opening paragraph acknowledging receipt: [Suggested opening]
Point-wise rebuttal or conditional acceptance: [Numbered points]
Instructions to Contractor: [Specific actions required]
Closure with reservation of Employer's rights: [Recommended closing]

Ensure all citations are precise and all assessments are grounded in the provided contract clauses and legal principles."""


class LLMConfigService:
    """Persist and resolve LangGraph LLM configuration for drafter/reviewer."""

    COLLECTION = "app_settings"
    DOC_ID = "langgraph_llm_config"

    def __init__(self, db=None):
        self._db = db

    async def _get_collection(self):
        # Motor databases do not support truthy checks; compare explicitly to None
        db = self._db if self._db is not None else await get_database()
        return db[self.COLLECTION]

    async def get_config(self) -> LangGraphLLMConfig:
        """Fetch current configuration with defaults applied."""
        coll = await self._get_collection()
        doc = await coll.find_one({"_id": self.DOC_ID}) or {}
        return LangGraphLLMConfig(
            drafter_model=doc.get("drafter_model")
            or getattr(settings, "LANGGRAPH_DRAFTER_MODEL", "gpt-4o"),
            reviewer_model=doc.get("reviewer_model")
            or getattr(settings, "LANGGRAPH_REVIEWER_MODEL", "gpt-4o-mini"),
            plan_model=doc.get("plan_model")
            or getattr(settings, "LANGGRAPH_PLAN_MODEL", "grok-4-1-fast"),
            draft_prompt_template=doc.get("draft_prompt_template")
            or getattr(settings, "LANGGRAPH_DRAFT_PROMPT_TEMPLATE", DEFAULT_DRAFT_PROMPT_TEMPLATE)
            or DEFAULT_DRAFT_PROMPT_TEMPLATE,
            plan_prompt_template=doc.get("plan_prompt_template")
            or getattr(settings, "LANGGRAPH_PLAN_PROMPT_TEMPLATE", DEFAULT_PLAN_PROMPT_TEMPLATE)
            or DEFAULT_PLAN_PROMPT_TEMPLATE,
            available_models=list(DEFAULT_LLM_MODELS),
            updated_at=doc.get("updated_at"),
        )

    async def update_config(self, payload: LangGraphLLMConfig) -> LangGraphLLMConfig:
        """Persist a new configuration, returning the stored copy."""
        coll = await self._get_collection()
        update_doc = {
            "drafter_model": payload.drafter_model,
            "reviewer_model": payload.reviewer_model,
            "plan_model": payload.plan_model,
            "draft_prompt_template": payload.draft_prompt_template,
            "plan_prompt_template": payload.plan_prompt_template,
            "updated_at": datetime.now(timezone.utc),
        }
        await coll.update_one(
            {"_id": self.DOC_ID},
            {"$set": update_doc},
            upsert=True,
        )
        return await self.get_config()


async def get_llm_config_service() -> LLMConfigService:
    """Convenience factory for dependency injection."""
    return LLMConfigService()
