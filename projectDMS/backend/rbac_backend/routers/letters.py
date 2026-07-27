# Improved letters.py



"""



Letter management with secure conversation handling, efficient querying, and clean architecture.



Addresses complex conversation logic while maintaining security and performance.



"""







from fastapi import APIRouter, Depends, HTTPException, Body, Query, status



from typing import List, Optional, Dict, Any, Union



from datetime import datetime, timezone



import logging



import asyncio







from ..core.security import get_current_user, CurrentUser, authorize_scope



from ..core.database import get_db



from ..services.letter_service import LetterService
from ..services.letter_drafting.service import DraftRunService



from ..dependencies import get_notification_service



from ..services.conversation_service import ConversationService  
from ..services.strategy_context_service import StrategyContextService


from ..services.authorization_service import AuthorizationService
from ..services.workflow import workflow_engine



from pydantic import BaseModel



from ..models.letter import (



    Letter, LetterCreate, LetterUpdate, ConversationTree, 



    ConversationSummary


)
from ..models.ai_models import StrategyContextRequest, StrategyContextResponse


from ..utils.validation import validate_input, sanitize_text



from ..utils.error_handler import handle_exceptions, LetterError



from ..utils.rate_limiter import RateLimiter



from ..utils.date_parser import parse_date_safely







logger = logging.getLogger(__name__)



router = APIRouter()







class LetterResponse(BaseModel):

    message: str


class ContextDocumentUpdateRequest(BaseModel):
    document_ids: List[str] = []


class StrategyRoleUpdateRequest(BaseModel):
    role: str
    recipient: Optional[str] = None


class GovernedTransitionRequest(BaseModel):
    draft_run_id: Optional[str] = None
    expected_draft_hash: Optional[str] = None


class SubmitLetterRequest(GovernedTransitionRequest):
    reviewer_summary: Optional[str] = None
    reviewer_findings: Optional[List[Dict[str, Any]]] = None







class AssignDrafterRequest(BaseModel):
    user_id: str
    drafting_profile: str


CONTRACT_DRAFTING_PROFILE_TO_STRATEGY_ROLE = {
    "contractor": "contractor",
    "engineer_representation": "engineer",
    "employer_contract_review": "employer",
}


class LetterController:



    """Letter controller with secure conversation management and efficient operations."""



    



    def __init__(



        self,



        letter_service: LetterService,



        conversation_service: ConversationService,



        auth_service: AuthorizationService,



        rate_limiter: RateLimiter



    ):



        self.letter_service = letter_service



        self.conversation_service = conversation_service



        self.auth_service = auth_service



        self.rate_limiter = rate_limiter







    async def get_letters(



        self,



        filters: Dict[str, Any],



        pagination: Dict[str, int],



        current_user: CurrentUser



    ) -> List[Letter]:



        """Get letters with secure filtering and efficient querying."""



        try:



            # Rate limiting



            await self.rate_limiter.check_user_limit(current_user.id)



            



            # Build authorized query



            authorized_query = await self.auth_service.build_letter_query(



                current_user, filters



            )



            



            if filters.get("search_query"):



                authorized_query["search_query"] = filters["search_query"]



            if filters.get("tab") and "status" not in authorized_query:



                authorized_query["tab"] = filters["tab"]







            letters = await self.letter_service.get_letters_paginated(



                authorized_query,



                pagination,



            )







            return letters

        except HTTPException:
            raise
        except Exception as e:

            logger.error(f"Failed to get letters: {str(e)}")

            raise HTTPException(



                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,



                detail="Letter service temporarily unavailable"



            )







    async def get_letter(



        self, letter_id: str, current_user: CurrentUser



    ) -> Letter:



        """Get single letter with authorization."""



        try:



            # Get letter



            letter = await self.letter_service.get_letter_by_id(letter_id)



            if not letter:



                raise LetterError("Letter not found", status.HTTP_404_NOT_FOUND)



            



            # Authorization check



            await self.auth_service.check_letter_access(



                current_user, letter, "read"



            )



            



            return letter



            



        except (LetterError, HTTPException):



            raise



        except Exception as e:



            logger.error(f"Failed to get letter {letter_id}: {str(e)}")



            raise HTTPException(



                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,



                detail="Letter service temporarily unavailable"



            )







    async def create_letter(



        self,



        letter_data: LetterCreate,



        current_user: CurrentUser



    ) -> Letter:



        """Create letter with conversation chain validation."""



        try:



            # Rate limiting



            await self.rate_limiter.check_user_limit(current_user.id)



            



            # Input validation and sanitization



            validated_data = await self._validate_letter_input(letter_data)



            



            # Authorization check



            await self.auth_service.check_letter_creation_permission(



                current_user, validated_data



            )



            



            # Handle conversation chain logic securely



            conversation_context = None



            if validated_data.previous_letter_id:



                conversation_context = await self._validate_conversation_chain(



                    validated_data.previous_letter_id, current_user



                )



            



            # Create letter with atomic transaction



            letter = await self.letter_service.create_letter_with_chain(



                validated_data, conversation_context, current_user



            )



            



            return letter



            



        except (LetterError, HTTPException):



            raise



        except Exception as e:



            logger.error(f"Failed to create letter: {str(e)}")



            raise HTTPException(



                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,



                detail="Letter creation service temporarily unavailable"



            )







    async def update_letter(



        self,



        letter_id: str,



        update_data: LetterUpdate,



        current_user: CurrentUser



    ) -> Letter:



        """Update letter with security validation."""



        try:



            # Get existing letter



            letter = await self.letter_service.get_letter_by_id(letter_id)



            if not letter:



                raise LetterError("Letter not found", status.HTTP_404_NOT_FOUND)



            



            # Authorization check



            await self.auth_service.check_letter_access(



                current_user, letter, "update"



            )



            



            # Validate update data



            validated_update = await self._validate_letter_update(update_data)



            



            # Perform update



            updated_letter = await self.letter_service.update_letter(



                letter_id, validated_update, current_user



            )



            



            return updated_letter



            



        except (LetterError, HTTPException):



            raise



        except Exception as e:



            logger.error(f"Failed to update letter {letter_id}: {str(e)}")



            raise HTTPException(



                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,



                detail="Letter update service temporarily unavailable"



            )







    async def get_conversation_chain(



        self, letter_id: str, current_user: CurrentUser



    ) -> List[Letter]:



        """Get conversation chain with efficient querying."""



        try:



            # Rate limiting for expensive operations



            await self.rate_limiter.check_user_limit(current_user.id, cost=5)



            



            # Get letter and authorize



            letter = await self.letter_service.get_letter_by_id(letter_id)



            if not letter:



                raise LetterError("Letter not found", status.HTTP_404_NOT_FOUND)



            



            await self.auth_service.check_letter_access(current_user, letter, "read")



            



            # Get chain efficiently



            chain = await self.conversation_service.get_conversation_chain(



                letter_id, current_user



            )



            



            return chain



            



        except (LetterError, HTTPException):



            raise



        except Exception as e:



            logger.error(f"Failed to get chain for {letter_id}: {str(e)}")



            raise HTTPException(



                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,



                detail="Conversation service temporarily unavailable"



            )







    async def get_conversation_tree(



        self, letter_id: str, current_user: CurrentUser



    ) -> ConversationTree:



        """Get conversation tree with security and efficiency."""



        try:



            # Rate limiting for expensive operations



            await self.rate_limiter.check_user_limit(current_user.id, cost=10)



            



            # Get letter and authorize



            letter = await self.letter_service.get_letter_by_id(letter_id)



            if not letter:



                raise LetterError("Letter not found", status.HTTP_404_NOT_FOUND)



            



            await self.auth_service.check_letter_access(current_user, letter, "read")



            



            # Build tree efficiently



            tree = await self.conversation_service.build_conversation_tree(



                letter.conversation_id, current_user



            )



            



            return tree



            



        except (LetterError, HTTPException):



            raise



        except Exception as e:



            logger.error(f"Failed to build tree for {letter_id}: {str(e)}")



            raise HTTPException(



                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,



                detail="Conversation service temporarily unavailable"



            )







    async def reparent_letter(



        self,



        letter_id: str,



        new_parent_id: str,



        current_user: CurrentUser



    ) -> LetterResponse:



        """Secure letter reparenting with cycle prevention."""



        try:



            # Rate limiting for expensive operations



            await self.rate_limiter.check_user_limit(current_user.id, cost=15)



            



            # Validate both letters exist and user has access



            letter = await self.letter_service.get_letter(letter_id)



            new_parent = await self.letter_service.get_letter(new_parent_id)



            



            if not letter or not new_parent:



                raise LetterError("Letter not found", status.HTTP_404_NOT_FOUND)



            



            # Authorization checks



            await self.auth_service.check_letter_access(current_user, letter, "update")



            await self.auth_service.check_letter_access(current_user, new_parent, "read")



            



            # Validate reparenting operation



            await self._validate_reparenting(letter, new_parent)



            



            # Perform reparenting with transaction



            await self.conversation_service.reparent_letter_atomic(



                letter_id, new_parent_id, current_user



            )



            



            return LetterResponse(message="Letter reparented successfully")



            



        except (LetterError, HTTPException):



            raise



        except Exception as e:



            logger.error(f"Reparenting failed for {letter_id}: {str(e)}")



            raise HTTPException(



                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,



                detail="Reparenting service temporarily unavailable"



            )







    async def _validate_letter_input(self, letter_data: LetterCreate) -> LetterCreate:



        """Comprehensive input validation and sanitization."""



        return LetterCreate(



            title=sanitize_text(validate_input(letter_data.title, max_length=500)),



            recipient=sanitize_text(validate_input(letter_data.recipient, max_length=500)),



            subject=sanitize_text(validate_input(letter_data.subject, max_length=1000)),



            content=sanitize_text(letter_data.content) if letter_data.content else "",



            assigned_to=validate_input(letter_data.assigned_to, required=True),



            organization_id=letter_data.organization_id,



            project_id=letter_data.project_id,



            previous_letter_id=letter_data.previous_letter_id,



            date=parse_date_safely(letter_data.date) if letter_data.date else None,



            from_party_id=letter_data.from_party_id,



            to_party_id=letter_data.to_party_id,



            letter_no=sanitize_text(letter_data.letter_no) if letter_data.letter_no else None,



            reference=letter_data.reference,



            references=letter_data.references or []



        )







    async def _validate_letter_update(self, update_data: LetterUpdate) -> LetterUpdate:



        """Validate update data with security checks."""



        validated_fields = {}



        



        if update_data.title is not None:



            validated_fields['title'] = sanitize_text(



                validate_input(update_data.title, max_length=500)



            )



        



        if update_data.subject is not None:



            validated_fields['subject'] = sanitize_text(



                validate_input(update_data.subject, max_length=1000)



            )



        



        if update_data.content is not None:



            validated_fields['content'] = sanitize_text(update_data.content)



        if update_data.recipient is not None:



            validated_fields['recipient'] = sanitize_text(



                validate_input(update_data.recipient, max_length=1000)



            )



        if update_data.status is not None:



            next_status = sanitize_text(



                validate_input(update_data.status, max_length=50)



            )



            if next_status not in {



                "Draft", "Input", "Strategy", "Review", "Approval", "Completed", "Rejected"



            }:



                raise LetterError("Invalid letter status", status.HTTP_400_BAD_REQUEST)



            validated_fields['status'] = next_status



        if update_data.assigned_to is not None:



            validated_fields['assigned_to'] = sanitize_text(



                validate_input(update_data.assigned_to, max_length=200)



            )



        if update_data.reference is not None:



            validated_fields['reference'] = update_data.reference



        if update_data.strategy_plan is not None:



            validated_fields['strategy_plan'] = sanitize_text(



                update_data.strategy_plan, max_length=50000



            )



        if update_data.strategic_outline is not None:



            validated_fields['strategic_outline'] = update_data.strategic_outline



        if update_data.summary_points is not None:



            validated_fields['summary_points'] = [



                sanitize_text(str(point), max_length=5000)



                for point in update_data.summary_points[:100]



                if str(point).strip()



            ]



        if update_data.strategy_role is not None:



            strategy_role = sanitize_text(



                validate_input(update_data.strategy_role, max_length=50)



            ).lower()



            if strategy_role not in {"contractor", "engineer", "employer"}:



                raise LetterError("Invalid strategy role", status.HTTP_400_BAD_REQUEST)



            validated_fields['strategy_role'] = strategy_role



        if update_data.strategy_recipient is not None:



            validated_fields['strategy_recipient'] = sanitize_text(



                update_data.strategy_recipient, max_length=200



            )

        if update_data.strategy_plan_approved_by is not None:

            validated_fields['strategy_plan_approved_by'] = sanitize_text(

                validate_input(update_data.strategy_plan_approved_by, max_length=200)

            )

        if update_data.strategy_plan_approved_at is not None:

            validated_fields['strategy_plan_approved_at'] = update_data.strategy_plan_approved_at

        if update_data.accepted_strategy_version is not None:

            validated_fields['accepted_strategy_version'] = update_data.accepted_strategy_version



        for text_field in (



            "contractor_context",



            "engineer_context",



            "employer_context",



            "draft_plan",



            "draft_output",



            "background_annotations",



        ):



            value = getattr(update_data, text_field, None)



            if value is not None:



                validated_fields[text_field] = sanitize_text(value, max_length=50000)



        for list_field in (



            "context_document_ids",



            "context_documents",



            "background_summary",



            "draft_sources",



            "reviewer_findings",



            "graph_warnings",



            "graph_thread",



            "draft_trace",



            "strategy_graph_trace",



        ):



            value = getattr(update_data, list_field, None)



            if value is not None:



                validated_fields[list_field] = value



        if update_data.reviewer_blocking is not None:



            validated_fields['reviewer_blocking'] = bool(update_data.reviewer_blocking)



        



        # Prevent conversation structure manipulation through update



        excluded_fields = {



            'conversation_id', 'previous_letter_id', 'ancestors', 'depth'



        }



        for field in excluded_fields:



            validated_fields.pop(field, None)



        



        return LetterUpdate(**validated_fields)







    async def _validate_conversation_chain(



        self, previous_letter_id: str, current_user: CurrentUser



    ) -> Dict[str, Any]:



        """Validate conversation chain to prevent cycles and security issues."""



        parent = await self.letter_service.get_letter_by_id(previous_letter_id)



        if not parent:



            raise LetterError(



                "Previous letter not found", 



                status.HTTP_400_BAD_REQUEST



            )



        



        # Authorization check on parent



        await self.auth_service.check_letter_access(current_user, parent, "read")



        



        # Build conversation context



        ancestors = parent.ancestors or []



        depth = parent.depth + 1



        



        # Validate depth limits (prevent extremely deep chains)



        if depth > 50:  # Reasonable limit



            raise LetterError(



                "Conversation chain too deep",



                status.HTTP_400_BAD_REQUEST



            )



        



        return {



            'parent': parent,



            'ancestors': ancestors + [str(parent.id)],



            'depth': depth,



            'conversation_id': parent.conversation_id or str(parent.id)



        }







    async def _validate_reparenting(



        self, letter: Letter, new_parent: Letter



    ) -> None:



        """Validate reparenting operation to prevent cycles."""



        # Check if letters are in same conversation



        if letter.conversation_id != new_parent.conversation_id:



            raise LetterError(



                "Letters must be in same conversation",



                status.HTTP_400_BAD_REQUEST



            )



        



        # Prevent self-parenting



        if str(letter.id) == str(new_parent.id):



            raise LetterError(



                "Letter cannot be parent of itself",



                status.HTTP_400_BAD_REQUEST



            )



        



        # Prevent cycles (new parent cannot be descendant)



        if str(new_parent.id) in (letter.ancestors or []):



            raise LetterError(



                "Cycle detected in reparenting",



                status.HTTP_400_BAD_REQUEST



            )







# Dependency injection



async def get_letter_controller(db=Depends(get_db)) -> LetterController:



    """Factory function for letter controller."""



    notification_service = await get_notification_service()



    letter_service = LetterService(db, notification_service=notification_service)



    conversation_service = ConversationService(letter_service)



    auth_service = AuthorizationService()



    rate_limiter = RateLimiter(
        max_requests=100,
        window_seconds=3600,
        scope="letters",
    )



    return LetterController(letter_service, conversation_service, auth_service, rate_limiter)











# API Endpoints with minimal logic



@router.get("/letters", response_model=List[Letter])



@handle_exceptions



async def get_letters(



    skip: int = Query(0, ge=0),



    limit: int = Query(100, ge=1, le=1000),



    status_filter: Optional[str] = Query(None, alias="status"),



    tab: Optional[str] = Query(None),



    organization_id: Optional[str] = Query(None),



    project_id: Optional[str] = Query(None),



    q: Optional[str] = Query(None),



    controller: LetterController = Depends(get_letter_controller),



    current_user: CurrentUser = Depends(get_current_user)



):



    """Get letters with filtering and pagination."""



    filters = {



        'status': status_filter or tab,



        'organization_id': organization_id,



        'project_id': project_id,



        'search_query': q



    }



    pagination = {'skip': skip, 'limit': limit}



    



    return await controller.get_letters(filters, pagination, current_user)











@router.get("/letters/{letter_id}", response_model=Letter)



@handle_exceptions



async def get_letter(



    letter_id: str,



    controller: LetterController = Depends(get_letter_controller),



    current_user: CurrentUser = Depends(get_current_user)



):



    """Get specific letter by ID."""



    return await controller.get_letter(letter_id, current_user)











@router.post("/letters", response_model=Letter)



@handle_exceptions



async def create_letter(



    letter_data: LetterCreate,



    controller: LetterController = Depends(get_letter_controller),



    current_user: CurrentUser = Depends(get_current_user)



):



    """Create new letter with conversation chain validation."""



    return await controller.create_letter(letter_data, current_user)











@router.put("/letters/{letter_id}", response_model=Letter)



@handle_exceptions



async def update_letter(



    letter_id: str,



    update_data: LetterUpdate,



    controller: LetterController = Depends(get_letter_controller),



    current_user: CurrentUser = Depends(get_current_user)



):



    """Update existing letter."""



    return await controller.update_letter(letter_id, update_data, current_user)


@router.post("/letters/{letter_id}/assign-drafter", response_model=Letter)
@handle_exceptions
async def assign_contract_drafter(
    letter_id: str,
    payload: AssignDrafterRequest,
    controller: LetterController = Depends(get_letter_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Assign a contract letter drafter and drafting representation profile."""
    letter = await controller.letter_service.get_letter_by_id(letter_id)
    if not letter:
        raise LetterError("Letter not found", status.HTTP_404_NOT_FOUND)

    await controller.auth_service.check_letter_access(current_user, letter, "update")
    can_assign = await controller.auth_service.has_permission(
        current_user,
        "drafting.request.assign",
        context={"resource_type": "letter", "resource_id": letter_id},
    )
    role_names = {str(role).lower() for role in (getattr(current_user, "roles", []) or [])}
    is_manager = bool(
        role_names
        & {
            "superadmin",
            "contraclaim_drafting_manager",
            "contract_manager",
            "contract manager",
            "headcontract",
            "contractmgr_org",
            "contractmgr_proj",
        }
    )
    if not can_assign and not is_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Contract Managers can assign contract letter drafters",
        )

    normalized_profile = str(payload.drafting_profile or "").strip().lower()
    if normalized_profile not in CONTRACT_DRAFTING_PROFILE_TO_STRATEGY_ROLE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid drafting profile",
        )

    updated = await controller.letter_service.update_letter(
        letter_id,
        {
            "assigned_to": validate_input(payload.user_id, required=True),
            "drafting_profile": normalized_profile,
            "strategy_role": CONTRACT_DRAFTING_PROFILE_TO_STRATEGY_ROLE[normalized_profile],
            "drafting_assigned_by": getattr(current_user, "id", None),
            "drafting_assigned_at": datetime.now(timezone.utc),
        },
        current_user,
    )
    if not updated:
        raise LetterError("Letter not found", status.HTTP_404_NOT_FOUND)
    # Two-way sync: assigning a drafter opens a Draft task on the board.
    try:
        from ..services.task_sync_service import TaskSyncService

        await TaskSyncService(controller.letter_service.db).on_drafter_assigned(
            updated, payload.user_id, getattr(current_user, "id", None)
        )
    except Exception:
        logger.debug("Draft task sync skipped for letter %s", letter_id, exc_info=True)
    return updated











@router.get("/letters/{letter_id}/chain", response_model=List[Letter])



@handle_exceptions



async def get_letter_chain(



    letter_id: str,



    controller: LetterController = Depends(get_letter_controller),



    current_user: CurrentUser = Depends(get_current_user)



):



    """Get conversation chain for a letter."""



    return await controller.get_conversation_chain(letter_id, current_user)











@router.get("/letters/{letter_id}/tree", response_model=ConversationTree)



@handle_exceptions



async def get_conversation_tree(



    letter_id: str,



    controller: LetterController = Depends(get_letter_controller),



    current_user: CurrentUser = Depends(get_current_user)



):



    """Get conversation tree structure."""



    return await controller.conversation_service.build_conversation_tree(letter_id, current_user)











@router.post("/letters/{letter_id}/reparent")



@handle_exceptions



async def reparent_letter(



    letter_id: str,



    payload: Dict[str, str] = Body(...),



    controller: LetterController = Depends(get_letter_controller),



    current_user: CurrentUser = Depends(get_current_user)



):



    """Reparent letter with cycle prevention."""



    new_parent_id = payload.get("new_parent_id")



    if not new_parent_id:



        raise HTTPException(



            status_code=status.HTTP_400_BAD_REQUEST,



            detail="new_parent_id is required"



        )



    



    await controller.conversation_service.reparent_letter_atomic(letter_id, new_parent_id, current_user)



    return {"message": "Reparented"}






@router.get("/letters/{letter_id}/context-documents")
@handle_exceptions
async def get_letter_context_documents(
    letter_id: str,
    controller: LetterController = Depends(get_letter_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return curated context documents for the specified letter."""
    await controller.get_letter(letter_id, current_user)
    return await controller.letter_service.get_context_documents(letter_id)


@router.put("/letters/{letter_id}/context-documents")
@handle_exceptions
async def update_letter_context_documents(
    letter_id: str,
    payload: ContextDocumentUpdateRequest,
    controller: LetterController = Depends(get_letter_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Persist curated context documents for a letter."""
    letter = await controller.get_letter(letter_id, current_user)
    await controller.auth_service.check_letter_access(current_user, letter, "write")
    return await controller.letter_service.save_context_documents(
        letter_id, payload.document_ids, current_user
    )




@router.post("/letters/{letter_id}/strategy/context", response_model=StrategyContextResponse)
@handle_exceptions
async def generate_letter_strategy_context(
    letter_id: str,
    request: StrategyContextRequest,
    controller: LetterController = Depends(get_letter_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Generate consolidated contexts for contractor, engineer, and employer perspectives."""
    await controller.get_letter(letter_id, current_user)
    context_service = StrategyContextService(
        letter_service=controller.letter_service,
        conversation_service=controller.conversation_service,
    )
    result = await context_service.generate_context(letter_id)
    await controller.letter_service.update_letter(
        letter_id,
        {
            "contractor_context": result.contractor_context,
            "engineer_context": result.engineer_context,
            "employer_context": result.employer_context,
            "thread_letters": result.thread_letters,
        },
    )
    return result


@router.patch("/letters/{letter_id}/strategy-role")
@handle_exceptions
async def update_strategy_role(
    letter_id: str,
    payload: StrategyRoleUpdateRequest,
    controller: LetterController = Depends(get_letter_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Persist the selected strategy role and target recipient."""
    await controller.get_letter(letter_id, current_user)
    await controller.letter_service.update_letter(
        letter_id,
        {
            "strategy_role": payload.role,
            "strategy_recipient": payload.recipient,
        },
    )
    return {"letter_id": letter_id, "role": payload.role, "recipient": payload.recipient}


# Status management endpoints

def _enforce_letter_separation_of_duties(letter: Any, current_user: CurrentUser) -> None:
    """Block self-approval: the approver must not be the letter's creator or
    assigned drafter. Mirrors ApprovalService._guard_decider for letters."""
    actor_id = str(getattr(current_user, "id", "") or "")
    if not actor_id:
        return

    def _field(obj: Any, *names: str) -> str:
        for name in names:
            value = getattr(obj, name, None)
            if value is None and isinstance(obj, dict):
                value = obj.get(name)
            if value:
                return str(value)
        return ""

    creator = _field(letter, "created_by", "createdBy")
    drafter = _field(letter, "assigned_to", "assignedTo", "drafting_assigned_to")
    if (creator or drafter) and actor_id in {creator, drafter}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Separation of duties: the drafter/creator cannot approve their own letter",
        )


def _enforce_letter_transition(letter: Any, new_status: str) -> None:
    """Reject invalid workflow jumps with a clean 409 (the state machine lives in
    services.workflow). Unrecognized legacy statuses can't be validated, so allow."""
    current = getattr(letter, "status", None)
    if current is None and isinstance(letter, dict):
        current = letter.get("status")
    if not current:
        return
    try:
        allowed = workflow_engine.can_transition(str(current), str(new_status))
    except Exception:
        return
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Invalid transition '{current}' -> '{new_status}'. "
                f"Allowed: {workflow_engine.get_valid_transitions(str(current))}"
            ),
        )


@router.post("/letters/{letter_id}/move-to-strategy")
@handle_exceptions
async def move_letter_to_strategy(
    letter_id: str,
    controller: LetterController = Depends(get_letter_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Transition a letter from Input to the Strategy stage."""
    letter = await controller.letter_service.get_letter(letter_id)
    if not letter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Letter not found",
        )
    await controller.auth_service.check_letter_access(current_user, letter, "admin")
    if (letter.status or "").lower() != "input":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot move letter in '{letter.status}' status to Strategy",
        )

    await controller.letter_service.change_status(
        letter_id,
        "Strategy",
        user_id=getattr(current_user, "id", None),
        validate_transition=True,
    )
    return {"letter_id": letter_id, "status": "Strategy"}

@router.post("/letters/{letter_id}/submit")


@handle_exceptions



async def submit_letter(



    letter_id: str,


    payload: Optional[SubmitLetterRequest] = Body(default=None),



    controller: LetterController = Depends(get_letter_controller),



    current_user: CurrentUser = Depends(get_current_user)



):



    """Submit a drafted letter for review (enforces scope)."""
    letter = await controller.letter_service.get_letter(letter_id)
    if not letter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Letter not found")
    await controller.auth_service.check_letter_access(current_user, letter, "admin")
    _enforce_letter_transition(letter, "Review")


    comment = None
    if payload:
        if payload.reviewer_summary:
            comment = payload.reviewer_summary
        elif payload.reviewer_findings:
            lines = []
            for finding in payload.reviewer_findings:
                if not isinstance(finding, dict):
                    continue
                level = str(finding.get("level") or "warning").upper()
                message = str(finding.get("message") or "").strip()
                if not message:
                    continue
                evidence = str(finding.get("evidence") or "").strip()
                line = f"{level}: {message}"
                if evidence:
                    line = f"{line} ({evidence})"
                lines.append(line)
            if lines:
                comment = "Reviewer findings:\n" + "\n".join(lines)

    await DraftRunService(controller.letter_service.db).approve_legacy_workflow_stage(
        letter_id,
        "drafter",
        letter.content,
        current_user,
        run_id=payload.draft_run_id if payload else None,
        expected_draft_hash=payload.expected_draft_hash if payload else None,
        comment=comment,
    )

    return await controller.letter_service.change_status(


        letter_id, "Review", comment, user_id=getattr(current_user, "id", None), validate_transition=True



    )











@router.post("/letters/{letter_id}/approve")



@handle_exceptions



async def approve_letter(



    letter_id: str,

    payload: Optional[GovernedTransitionRequest] = Body(default=None),



    controller: LetterController = Depends(get_letter_controller),



    current_user: CurrentUser = Depends(get_current_user)



):



    """Move a reviewed letter into the approval stage (enforces scope + SoD)."""
    letter = await controller.letter_service.get_letter(letter_id)
    if not letter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Letter not found")
    await controller.auth_service.check_letter_access(current_user, letter, "admin")
    _enforce_letter_separation_of_duties(letter, current_user)
    _enforce_letter_transition(letter, "Approval")

    await DraftRunService(controller.letter_service.db).approve_legacy_workflow_stage(
        letter_id,
        "reviewer",
        letter.content,
        current_user,
        run_id=payload.draft_run_id if payload else None,
        expected_draft_hash=payload.expected_draft_hash if payload else None,
    )



    return await controller.letter_service.change_status(



        letter_id, "Approval", user_id=getattr(current_user, "id", None), validate_transition=True



    )







@router.post("/letters/{letter_id}/complete")



@handle_exceptions



async def complete_letter(



    letter_id: str,

    payload: Optional[GovernedTransitionRequest] = Body(default=None),



    controller: LetterController = Depends(get_letter_controller),



    current_user: CurrentUser = Depends(get_current_user)



):



    """Finalize an approved letter as completed (enforces scope + SoD)."""
    letter = await controller.letter_service.get_letter(letter_id)
    if not letter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Letter not found")
    await controller.auth_service.check_letter_access(current_user, letter, "admin")
    _enforce_letter_separation_of_duties(letter, current_user)
    _enforce_letter_transition(letter, "Completed")

    await DraftRunService(controller.letter_service.db).approve_legacy_workflow_stage(
        letter_id,
        "final",
        letter.content,
        current_user,
        run_id=payload.draft_run_id if payload else None,
        expected_draft_hash=payload.expected_draft_hash if payload else None,
    )



    return await controller.letter_service.change_status(



        letter_id, "Completed", user_id=getattr(current_user, "id", None), validate_transition=True



    )

