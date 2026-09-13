# Improved ai_assistant.py
"""
AI Assistant module with clean architecture, proper security, and performance optimizations.
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, BackgroundTasks
from typing import List, Optional, Dict, Any, Tuple, Union
from datetime import datetime, timedelta
import hashlib
import logging
import asyncio
from functools import wraps
from types import SimpleNamespace

from ..core.permissions import Permissions
from ..core.security import get_current_user, CurrentUser
from ..core.config import settings
from ..services.ai_service import AIService
from ..services.llm_config_service import LLMConfigService
from ..services.cache_service import CacheService
from ..services.policy_service import PolicyService
from ..services.usage_metering_service import UsageEventType
from ..utils.rate_limiter import RateLimiter
from ..models.ai_models import (
    LangGraphDraftRequest,
    LangGraphDraftResponse,
    LetterDraftRequest,
    LetterDraftResponse,
    VectorSearchResponse,
    LetterSearchRequest,
    SimilarLetter,
    AIAssistantStats,
    StyleProfileResponse,
    StrategyPlanRequest,
    StrategyPlanResponse,
    LangGraphLLMConfig,
)
from ..utils.validation import validate_input, sanitize_text
from ..utils.error_handler import BaseDomainError, handle_exceptions

# Configure structured logging
logger = logging.getLogger(__name__)
router = APIRouter()


def _require_platform_admin(current_user: CurrentUser) -> None:
    roles = {str(role).lower() for role in (current_user.roles or [])}
    if "superadmin" in roles:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Platform administrator role required",
    )


class AIAssistantController:
    """Clean controller with proper dependency injection."""

    def __init__(
        self,
        ai_service: AIService,
        cache_service: CacheService,
        rate_limiter: RateLimiter,
        llm_config_service: LLMConfigService,
        policy_service: Optional[PolicyService] = None,
    ):
        self.ai_service = ai_service
        self.cache_service = cache_service
        self.rate_limiter = rate_limiter
        self.llm_config_service = llm_config_service
        self.policy_service = policy_service or PolicyService()

    def _resolve_request_scope(
        self,
        request: Any,
        current_user: CurrentUser,
    ) -> tuple[Optional[str], Optional[str]]:
        org_id = getattr(request, "organization_id", None) or getattr(current_user, "organization_id", None)
        project_id = getattr(request, "project_id", None)
        if project_id is None:
            project_id = (getattr(current_user, "projects", []) or [None])[0]
        return org_id, project_id

    async def _authorize_ai_action(
        self,
        request: Any,
        current_user: CurrentUser,
        permission: str,
        *,
        resource_type: str,
        resource_id: Optional[str] = None,
        meter_event_type: Optional[str] = None,
        meter_quantity: int = 1,
        meter_metadata: Optional[Dict[str, Any]] = None,
        audit: bool = True,
    ) -> tuple[Optional[str], Optional[str]]:
        org_id, project_id = self._resolve_request_scope(request, current_user)
        await self.policy_service.authorize(
            current_user,
            permission,
            resource_type=resource_type,
            resource_id=resource_id,
            organization_id=org_id,
            project_id=project_id,
            meter_event_type=meter_event_type,
            meter_quantity=meter_quantity,
            meter_metadata=meter_metadata,
            audit=audit,
        )
        return org_id, project_id

    async def search_similar_letters(
        self,
        request: LetterSearchRequest,
        current_user: CurrentUser
    ) -> VectorSearchResponse:
        """Search for semantically similar letters with proper security and caching."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)

            # Input validation and sanitization
            query = sanitize_text(validate_input(request.query, max_length=1000))
            org_id, project_id = await self._authorize_ai_action(
                request,
                current_user,
                Permissions.DOCUMENT_VIEW,
                resource_type="ai_assistant_letter_search",
                meter_event_type=UsageEventType.ADVANCED_SEARCH,
                meter_metadata={"operation": "search_similar_letters"},
                audit=False,
            )

            # Check cache first. hashlib, not hash(): Python's hash() is
            # randomized per process (PYTHONHASHSEED), so keys built with it
            # never match across workers or restarts and the cache was
            # effectively disabled in multi-worker deploys.
            query_digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:24]
            cache_key = (
                f"search:{current_user.id}:{org_id or 'global'}:"
                f"{project_id or 'global'}:{query_digest}:{request.limit}"
            )
            cached_result = await self.cache_service.get(cache_key)

            if cached_result:
                # The digest, not the query. `observability/service.py::
                # _redact_query` already reduces this exact value to
                # `[redacted len=N]`, so the repository's own position is that
                # a search query is sensitive; this line rendered 50 characters
                # of it at INFO. The digest is already computed above and is
                # what makes a cache hit diagnosable.
                logger.info(
                    "Cache hit for search query digest=%s len=%s",
                    query_digest,
                    len(query),
                )
                return VectorSearchResponse(**cached_result, cached_results=True)

            # Perform search
            result = await self.ai_service.search_similar_letters(
                query,
                current_user,
                request.limit,
                organization_id=org_id,
                project_id=project_id,
            )

            # Cache result
            await self.cache_service.set(
                cache_key, result.model_dump(), ttl=1800
            )

            return result

        except (BaseDomainError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Search failed for user {current_user.id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Search service temporarily unavailable"
            )

    async def generate_letter_draft(
        self,
        request: LetterDraftRequest,
        current_user: CurrentUser
    ) -> LetterDraftResponse:
        """Generate AI letter draft with comprehensive validation and security."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)

            org_id, project_id = await self._authorize_ai_action(
                request,
                current_user,
                Permissions.DRAFTING_REQUEST_CREATE,
                resource_type="ai_assistant_draft",
                resource_id=getattr(request, "letter_id", None),
                meter_event_type=UsageEventType.DRAFTED_LETTER,
                meter_metadata={"operation": "generate_letter_draft"},
            )

            # Input validation and sanitization
            sanitized_request = await self._sanitize_draft_request(request)
            sanitized_request = sanitized_request.model_copy(
                update={"organization_id": org_id, "project_id": project_id}
            )

            # Generate draft
            result = await self.ai_service.generate_draft(
                sanitized_request, current_user
            )

            logger.info(
                f"Draft generated for user {current_user.id}, "
                f"subject: {request.subject[:50]}..."
            )

            return result

        except (BaseDomainError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Draft generation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Draft generation service temporarily unavailable"
            )

    async def generate_langgraph_draft(
        self,
        request: LangGraphDraftRequest,
        current_user: CurrentUser,
    ) -> LangGraphDraftResponse:
        """Execute LangGraph pipeline for the requested letter."""
        try:
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            org_id, project_id = await self._authorize_ai_action(
                request,
                current_user,
                Permissions.DRAFTING_REQUEST_CREATE,
                resource_type="ai_assistant_langgraph_draft",
                resource_id=request.letter_id,
                meter_event_type=(
                    UsageEventType.AI_REVIEW
                    if getattr(request, "analysis_only", False)
                    else UsageEventType.DRAFTED_LETTER
                ),
                meter_metadata={
                    "operation": (
                        "generate_langgraph_background"
                        if getattr(request, "analysis_only", False)
                        else "generate_langgraph_draft"
                    ),
                    "analysis_only": bool(getattr(request, "analysis_only", False)),
                },
            )
            sanitized = await self._sanitize_draft_request(request)
            if not isinstance(sanitized, LangGraphDraftRequest):
                sanitized = LangGraphDraftRequest(**sanitized.model_dump())
            sanitized = sanitized.model_copy(
                update={"organization_id": org_id, "project_id": project_id}
            )

            result = await self.ai_service.generate_draft_with_langgraph(
                sanitized,
                current_user,
            )
            return result
        except (BaseDomainError, HTTPException):
            raise
        except Exception as exc:
            logger.error("LangGraph draft failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="LangGraph drafting temporarily unavailable",
            )

    async def generate_strategy_plan(
        self,
        request: StrategyPlanRequest,
        current_user: CurrentUser,
    ) -> StrategyPlanResponse:
        """Generate the structured Strategy-stage plan via LangGraph."""
        try:
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            org_id, project_id = await self._authorize_ai_action(
                request,
                current_user,
                Permissions.DRAFTING_REQUEST_CREATE,
                resource_type="ai_assistant_strategy_plan",
                resource_id=request.letter_id,
                meter_event_type=UsageEventType.AI_REVIEW,
                meter_metadata={"operation": "generate_strategy_plan"},
            )
            scoped_request = request.model_copy(
                update={"organization_id": org_id, "project_id": project_id}
            )
            return await self.ai_service.generate_strategy_plan(scoped_request, current_user)
        except (BaseDomainError, HTTPException):
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("LangGraph strategy plan failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="LangGraph strategy planning temporarily unavailable",
            )

    async def get_langgraph_run(
        self,
        letter_id: str,
        current_user: CurrentUser,
    ) -> LangGraphDraftResponse:
        """Retrieve the last LangGraph run for a letter."""
        await self.rate_limiter.check_user_limit(current_user.id)
        request_scope = SimpleNamespace(
            organization_id=getattr(current_user, "organization_id", None),
            project_id=(getattr(current_user, "projects", []) or [None])[0]
            if getattr(current_user, "projects", None)
            else None,
        )
        org_id, project_id = await self._authorize_ai_action(
            request_scope,
            current_user,
            Permissions.DRAFTING_REQUEST_VIEW,
            resource_type="ai_assistant_langgraph_run",
            resource_id=letter_id,
        )
        snapshot = await self.ai_service.get_latest_langgraph_run(
            letter_id,
            organization_id=org_id,
            project_id=project_id,
        )
        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No LangGraph run recorded for this letter",
            )
        return snapshot

    async def get_langgraph_config(
        self,
        current_user: CurrentUser,
    ) -> LangGraphLLMConfig:
        """Return current LangGraph LLM configuration (superadmin only)."""
        _require_platform_admin(current_user)
        return await self.llm_config_service.get_config()

    async def update_langgraph_config(
        self,
        payload: LangGraphLLMConfig,
        current_user: CurrentUser,
    ) -> LangGraphLLMConfig:
        """Update LangGraph LLM configuration (superadmin only)."""
        _require_platform_admin(current_user)
        return await self.llm_config_service.update_config(payload)

    async def _sanitize_draft_request(
        self, request: LetterDraftRequest
    ) -> LetterDraftRequest:
        """Sanitize and validate draft request to prevent injection attacks."""
        sanitized_cls = request.__class__
        payload = {
            "subject": sanitize_text(validate_input(request.subject, max_length=500)),
            "recipient": sanitize_text(validate_input(request.recipient, max_length=500)),
            "context": sanitize_text(request.context) if request.context else None,
            "points": sanitize_text(request.points) if request.points else None,
            "user_id": request.user_id,
            "organization_id": request.organization_id,
            "project_id": request.project_id,
            "document_ids": request.document_ids,
            "use_vector_store": request.use_vector_store,
            "letter_id": request.letter_id,
            "analysis_only": getattr(request, "analysis_only", False),
            "plan_override": sanitize_text(request.plan_override)
            if getattr(request, "plan_override", None)
            else None,
            "include_letter_codes": getattr(request, "include_letter_codes", []),
            "exclude_letter_codes": getattr(request, "exclude_letter_codes", []),
        }
        return sanitized_cls(**payload)


# Dependency injection
async def get_ai_controller() -> AIAssistantController:
    """Factory function for AI controller with proper dependencies."""
    ai_service = AIService()
    cache_service = CacheService()
    rate_limiter = RateLimiter(
        max_requests=settings.USER_RATE_LIMIT_REQUESTS,
        window_seconds=settings.USER_RATE_LIMIT_WINDOW,
        scope="ai_assistant",
    )
    llm_config_service = LLMConfigService()
    return AIAssistantController(ai_service, cache_service, rate_limiter, llm_config_service)


# API Endpoints with minimal logic
@router.post("/ai-assistant/search-letters", response_model=VectorSearchResponse)
@handle_exceptions
async def search_similar_letters(
    request: LetterSearchRequest,
    controller: AIAssistantController = Depends(get_ai_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Search for semantically similar letters."""
    return await controller.search_similar_letters(request, current_user)


@router.post("/ai-assistant/generate-draft", response_model=LetterDraftResponse)
@handle_exceptions
async def generate_letter_draft(
    request: LetterDraftRequest,
    controller: AIAssistantController = Depends(get_ai_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Generate comprehensive letter draft with AI assistance."""
    return await controller.generate_letter_draft(request, current_user)

@router.post("/ai-assistant/langgraph/draft", response_model=LangGraphDraftResponse)
@handle_exceptions
async def generate_langgraph_draft(
    request: LangGraphDraftRequest,
    controller: AIAssistantController = Depends(get_ai_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Run the LangGraph letter drafting pipeline."""
    return await controller.generate_langgraph_draft(request, current_user)


@router.post("/ai-assistant/langgraph/background", response_model=LangGraphDraftResponse)
@handle_exceptions
async def generate_langgraph_background(
    request: LangGraphDraftRequest,
    controller: AIAssistantController = Depends(get_ai_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Generate LangGraph background without drafting the reply."""
    request_with_flag = request.copy(update={"analysis_only": True})
    return await controller.generate_langgraph_draft(request_with_flag, current_user)


@router.post("/ai-assistant/langgraph/strategy-plan", response_model=StrategyPlanResponse)
@handle_exceptions
async def generate_langgraph_strategy_plan(
    request: StrategyPlanRequest,
    controller: AIAssistantController = Depends(get_ai_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Generate the structured plan for the Strategy stage."""
    return await controller.generate_strategy_plan(request, current_user)


@router.get("/ai-assistant/stats", response_model=AIAssistantStats)
@handle_exceptions
async def get_ai_assistant_stats(
    controller: AIAssistantController = Depends(get_ai_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get AI assistant usage statistics."""
    _require_platform_admin(current_user)
    return await controller.ai_service.get_stats(current_user)


@router.delete("/ai-assistant/cache")
@handle_exceptions
async def clear_cache(
    controller: AIAssistantController = Depends(get_ai_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Clear AI assistant caches."""
    _require_platform_admin(current_user)
    await controller.cache_service.clear_all()
    return {"status": "success", "timestamp": datetime.utcnow().isoformat()}


@router.get("/ai-assistant/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "ai-assistant",
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/ai-assistant/langgraph/config", response_model=LangGraphLLMConfig)
@handle_exceptions
async def get_langgraph_config(
    controller: AIAssistantController = Depends(get_ai_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch current LangGraph LLM configuration (superadmin/admin)."""
    return await controller.get_langgraph_config(current_user)


@router.put("/ai-assistant/langgraph/config", response_model=LangGraphLLMConfig)
@handle_exceptions
async def update_langgraph_config(
    payload: LangGraphLLMConfig,
    controller: AIAssistantController = Depends(get_ai_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update LangGraph LLM configuration (superadmin/admin)."""
    return await controller.update_langgraph_config(payload, current_user)

@router.get("/ai-assistant/langgraph/runs/{letter_id}", response_model=LangGraphDraftResponse)
@handle_exceptions
async def get_langgraph_run(
    letter_id: str,
    controller: AIAssistantController = Depends(get_ai_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch the latest LangGraph run for the specified letter."""
    return await controller.get_langgraph_run(letter_id, current_user)


from pydantic import BaseModel

class PromptTemplateUpdateRequest(BaseModel):
    template: str


@router.get("/ai-assistant/prompts")
@handle_exceptions
async def get_prompts(
    current_user: CurrentUser = Depends(get_current_user),
):
    """List letter drafting prompt keys and metadata (superadmin only)."""
    _require_platform_admin(current_user)

    from ..services.letter_drafting.prompts import (
        PromptRegistry,
        STRATEGY_PROMPT_KEY,
        DRAFT_PROMPT_KEY,
    )
    from ..core.database import get_database

    db = await get_database()
    registry = PromptRegistry(db)

    strategy = await registry.get_enabled(STRATEGY_PROMPT_KEY)
    draft = await registry.get_enabled(DRAFT_PROMPT_KEY)

    return [
        {
            "prompt_key": STRATEGY_PROMPT_KEY,
            "label": "Strategic Plan Prompt Template",
            "description": "Guides the generation of reply strategy plans and roadmaps.",
            "version": strategy.version,
            "supported_payload_schema": strategy.supported_payload_schema,
            "template": strategy.template,
        },
        {
            "prompt_key": DRAFT_PROMPT_KEY,
            "label": "Letter Drafting Prompt Template",
            "description": "Guides the generation of the actual formal reply letter draft.",
            "version": draft.version,
            "supported_payload_schema": draft.supported_payload_schema,
            "template": draft.template,
        }
    ]


@router.get("/ai-assistant/prompts/{prompt_key}")
@handle_exceptions
async def get_prompt_by_key(
    prompt_key: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch the latest enabled prompt template configuration (superadmin only)."""
    _require_platform_admin(current_user)

    from ..services.letter_drafting.prompts import PromptRegistry
    from ..core.database import get_database

    db = await get_database()
    registry = PromptRegistry(db)

    record = await registry.get_enabled(prompt_key)
    return record


@router.put("/ai-assistant/prompts/{prompt_key}")
@handle_exceptions
async def update_prompt_template(
    prompt_key: str,
    payload: PromptTemplateUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Modify/create a new enabled version of a prompt template (superadmin only)."""
    _require_platform_admin(current_user)

    from ..services.letter_drafting.prompts import (
        PromptRegistry,
        STRATEGY_PROMPT_KEY,
        DRAFT_PROMPT_KEY,
    )
    from ..core.database import get_database

    required_variables = set()
    if prompt_key == STRATEGY_PROMPT_KEY:
        required_variables = {"active_workspace", "role", "recipient", "subject", "recipient_focus", "current_materials", "sources"}
    elif prompt_key == DRAFT_PROMPT_KEY:
        required_variables = {"role", "active_workspace", "recipient", "subject", "recipient_focus", "current_materials", "plan", "sources", "profile_pattern"}
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid prompt key: {prompt_key}"
        )

    missing = PromptRegistry.validate_template(payload.template, required_variables)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing required template placeholder variables: {', '.join(missing)}"
        )

    db = await get_database()
    registry = PromptRegistry(db)

    user_id = getattr(current_user, "id", None) or getattr(current_user, "email", None) or "superadmin"
    record = await registry.update_prompt(prompt_key, payload.template, user_id)
    return record
