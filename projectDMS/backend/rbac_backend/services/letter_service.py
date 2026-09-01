import logging
import re
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING
from datetime import datetime, timezone

from pymongo.database import Database
from bson.objectid import ObjectId
from bson.errors import InvalidId

from ..models.letter import Letter
from ..models.notification import NotificationContext, NotificationType
from ..models.ai_models import (
    LangGraphDraftResponse,
    LangGraphNodeTrace,
    LetterDraftResponse,
)
from ..models.document import Document
from ..utils.notification_service import NotificationService
from ..utils.date_parser import parse_date_safely
from .workflow import WorkflowUtils, workflow_engine
from .common import fetch_paginated, validate_pagination
from .document_service import (
    DocumentService,
    DocumentServiceError,
    DocumentNotFoundError,
)

if TYPE_CHECKING:
    from ..ai_workflows.langgraph.letter_pipeline import LetterGraphResult

logger = logging.getLogger(__name__)

class LetterServiceError(Exception):
    """Custom exception for letter service errors"""
    pass

class LetterNotFoundError(LetterServiceError):
    """Exception raised when letter is not found"""
    pass

class InvalidLetterIdError(LetterServiceError):
    """Exception raised when letter ID is invalid"""
    pass

class InvalidLetterTransitionError(LetterServiceError):
    """Exception raised when a workflow status transition is not allowed."""
    pass

class LetterService:
    """Async service for letter CRUD operations with proper error handling"""
    
    def __init__(self, db: Database, notification_service: Optional[NotificationService] = None):
        if db is None:
            raise ValueError("Database connection cannot be None")
        self.db = db
        self.notification_service = notification_service
    
    def _validate_letter_id(self, letter_id: str) -> ObjectId:
        """
        Validate and convert letter ID to ObjectId.
        
        Args:
            letter_id: Letter ID string to validate
            
        Returns:
            ObjectId instance
            
        Raises:
            InvalidLetterIdError: If ID format is invalid
        """
        if not letter_id or not isinstance(letter_id, str):
            raise InvalidLetterIdError(f"Invalid letter ID: {letter_id}")
        
        try:
            return ObjectId(letter_id)
        except InvalidId as e:
            raise InvalidLetterIdError(f"Invalid ObjectId format: {letter_id}") from e
    
    async def get_letters(
        self,
        skip: int = 0,
        limit: int = 50,
        filters: Optional[Dict[str, Any]] = None,
        search_query: Optional[str] = None,
    ) -> List[Optional[Letter]]:
        """
        Retrieve multiple letters with pagination and optional filtering.

        Args:
            skip: Number of letters to skip
            limit: Maximum number of letters to return
            filters: Optional filter dictionary (status/org/project)
            search_query: Optional free-text search term

        Returns:
            List of Letter instances

        Raises:
            LetterServiceError: If retrieval fails
        """
        try:
            skip, limit = validate_pagination(skip, limit)

            filters = filters or {}
            query: Dict[str, Any] = {}

            for key in ("status", "organization_id", "project_id"):
                value = filters.get(key)
                if value and str(value).strip().lower() != "all":
                    query[key] = value

            effective_search = search_query or filters.get("search_query")
            if effective_search:
                pattern = {"$regex": re.escape(str(effective_search)), "$options": "i"}
                query["$or"] = [
                    {"title": pattern},
                    {"subject": pattern},
                    {"recipient": pattern},
                    {"letter_no": pattern},
                ]

            logger.info(
                "Retrieving letters: skip=%s, limit=%s, query_keys=%s",
                skip,
                limit,
                sorted(query.keys()),
            )

            raw_letters, _ = await fetch_paginated(
                self.db.letters,
                filter=query,
                sort=[("updatedAt", -1)],
                skip=skip,
                limit=limit,
            )
            letters: List[Optional[Letter]] = []

            for letter_data in raw_letters:
                try:
                    prepared = self._augment_letter_document(letter_data)
                    letter = Letter(**prepared) if prepared else None
                    letters.append(letter)
                except Exception as e:
                    logger.warning(
                        "Failed to parse letter %s: %s",
                        letter_data.get("_id", "unknown") if isinstance(letter_data, dict) else "unknown",
                        e,
                    )
                    letters.append(None)

            logger.info("Retrieved %s letters", len(letters))
            return letters

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to retrieve letters: {e}")
            raise LetterServiceError(f"Letter retrieval failed: {str(e)}")

    async def authorized_letter_ids(
        self,
        letter_ids: List[str],
        current_user: Any,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Set[str]:
        """Of these letter ids, the ones the ACTOR may actually see.

        `get_letters` takes no actor. It filters on whatever `organization_id` /
        `project_id` the caller hands it and DROPS a falsy value entirely, so a
        letter with no project silently widens a "related correspondence" lookup
        to the whole organisation - and a caller that passes neither gets every
        tenant's letters.

        This answers row visibility the one canonical way, through
        `build_scope_query`, and returns ids rather than rows so a caller can
        filter a list it already holds without a second parse. Any supplied
        organisation/project NARROWS inside the actor's entitlement - a value
        outside it denies every row - and is never the authority source. No
        role name is read here.

        `current_user` carries no default: a helper over an unbounded list must
        fail closed by construction, not by each caller remembering to pass one.
        """
        if not letter_ids or current_user is None:
            return set()

        from ..core.security import build_scope_query, _expand_object_ids

        scope = build_scope_query(
            current_user,
            organization_id=organization_id,
            project_id=project_id,
        )

        identifiers = [str(letter_id) for letter_id in letter_ids if letter_id]
        if not identifiers:
            return set()

        id_clause: Dict[str, Any] = {"_id": {"$in": _expand_object_ids(identifiers)}}
        query = {"$and": [id_clause, scope]} if scope else id_clause

        rows = await self.db.letters.find(query, {"_id": 1}).to_list(length=None)
        return {str(row.get("_id")) for row in rows}

    async def get_letter(self, letter_id: str) -> Optional[Letter]:
        """
        Retrieve a single letter by ID.

        Args:
            letter_id: Letter ID string

        Returns:
            Letter instance if found, None otherwise

        Raises:
            LetterServiceError: If retrieval fails
            InvalidLetterIdError: If letter ID is invalid
        """
        try:
            letter_oid = self._validate_letter_id(letter_id)

            logger.debug(f"Retrieving letter: {letter_id}")

            letter_data = await self.db.letters.find_one({"_id": letter_oid})

            if not letter_data:
                logger.info(f"Letter not found: {letter_id}")
                return None

            prepared = self._augment_letter_document(letter_data)
            letter = Letter(**prepared)
            logger.info(f"Retrieved letter: {letter_id}")
            return letter

        except InvalidLetterIdError:
            raise
        except Exception as e:
            logger.error(f"Failed to retrieve letter {letter_id}: {e}")
            raise LetterServiceError(f"Letter retrieval failed: {str(e)}")


    def _augment_letter_document(self, letter_data: Any) -> Dict[str, Any]:
        """Prepare raw letter data with workflow metadata."""
        if not isinstance(letter_data, dict):
            return {}

        data: Dict[str, Any] = dict(letter_data)

        def _stringify_object_id(value: Any) -> Any:
            """Return ObjectId values as strings to satisfy Pydantic validation."""
            if isinstance(value, ObjectId):
                return str(value)
            return value

        if "_id" in data:
            data["_id"] = _stringify_object_id(data["_id"])

        for key in (
            "organization_id",
            "project_id",
            "created_by",
            "assigned_to",
            "conversation_id",
            "previous_letter_id",
        ):
            if key in data:
                data[key] = _stringify_object_id(data[key])

        ancestors = data.get("ancestors")
        if isinstance(ancestors, list):
            data["ancestors"] = [_stringify_object_id(a) for a in ancestors]

        references_raw = data.get("references")
        if isinstance(references_raw, list):
            normalised_refs: List[Any] = []
            for ref in references_raw:
                if isinstance(ref, dict):
                    ref_dict = dict(ref)
                    if "letter_id" in ref_dict:
                        ref_dict["letter_id"] = _stringify_object_id(ref_dict["letter_id"])
                    normalised_refs.append(ref_dict)
                else:
                    normalised_refs.append(ref)
            data["references"] = normalised_refs

        history_raw = data.get("status_history") or data.get("statusHistory") or []
        normalized_history = []
        if isinstance(history_raw, list):
            for entry in history_raw:
                if isinstance(entry, dict):
                    item = dict(entry)
                    changed = item.get("changed_at") or item.get("changedAt")
                    actor = item.get("actor_id") or item.get("actorId")
                    if actor is not None:
                        item["actor_id"] = _stringify_object_id(actor)
                    if changed:
                        try:
                            if isinstance(changed, datetime):
                                if changed.tzinfo is None:
                                    item["changed_at"] = changed.replace(tzinfo=timezone.utc)
                                else:
                                    item["changed_at"] = changed.astimezone(timezone.utc)
                            else:
                                item["changed_at"] = parse_date_safely(changed)
                        except Exception:
                            item.pop("changed_at", None)
                    normalized_history.append(item)
                else:
                    normalized_history.append(entry)
        data["status_history"] = normalized_history

        versions_raw = data.get("draft_versions")
        if isinstance(versions_raw, list):
            normalized_versions: List[Dict[str, Any]] = []
            for version in versions_raw:
                if not isinstance(version, dict):
                    continue
                item = dict(version)
                creator = item.get("created_by")
                if creator is not None:
                    item["created_by"] = _stringify_object_id(creator)
                created_at = item.get("created_at")
                if isinstance(created_at, datetime) and created_at.tzinfo is None:
                    item["created_at"] = created_at.replace(tzinfo=timezone.utc)
                normalized_versions.append(item)
            data["draft_versions"] = normalized_versions

        strategy_versions_raw = data.get("strategy_versions")
        if isinstance(strategy_versions_raw, list):
            normalized_strategy_versions: List[Dict[str, Any]] = []
            for version in strategy_versions_raw:
                if not isinstance(version, dict):
                    continue
                item = dict(version)
                creator = item.get("created_by")
                if creator is not None:
                    item["created_by"] = _stringify_object_id(creator)
                created_at = item.get("created_at")
                if isinstance(created_at, datetime) and created_at.tzinfo is None:
                    item["created_at"] = created_at.replace(tzinfo=timezone.utc)
                normalized_strategy_versions.append(item)
            data["strategy_versions"] = normalized_strategy_versions

        status_start = (
            data.get("statusStartDate")
            or data.get("status_start_date")
        )
        if not status_start and normalized_history:
            last = normalized_history[-1]
            if isinstance(last, dict):
                status_start = last.get("changed_at") or last.get("changedAt")

        if not status_start:
            status_start = (
                data.get("updatedAt")
                or data.get("updated_at")
                or data.get("createdAt")
                or data.get("created_at")
            )

        status_start_dt = None
        if status_start:
            try:
                if isinstance(status_start, datetime):
                    status_start_dt = (
                        status_start if status_start.tzinfo else status_start.replace(tzinfo=timezone.utc)
                    )
                else:
                    status_start_dt = parse_date_safely(status_start)
            except Exception:
                status_start_dt = None

        if status_start_dt:
            data["statusStartDate"] = status_start_dt
            data["status_start_date"] = status_start_dt
            pendency = WorkflowUtils.compute_pendency_days(status_start_dt)
            data["pendencyDays"] = pendency
            data["pendency_days"] = pendency
        else:
            data.setdefault("statusStartDate", None)
            data.setdefault("status_start_date", None)
            data.setdefault("pendencyDays", 0)
            data.setdefault("pendency_days", 0)

        return data

    async def create_letter(self, letter: Letter) -> Letter:
        """
        Create a new letter.

        Args:
            letter: Letter instance to create

        Returns:
            Created letter with assigned ID

        Raises:
            LetterServiceError: If creation fails
        """
        try:
            if not isinstance(letter, Letter):
                raise ValueError("letter must be a Letter instance")

            logger.info("Creating new letter")

            letter_dict = letter.model_dump(by_alias=True, exclude_unset=True)
            letter_dict.pop('_id', None)

            now = datetime.now(timezone.utc)
            letter_dict['createdAt'] = now
            letter_dict['updatedAt'] = now

            status_value = letter_dict.get('status') or 'Draft'
            letter_dict['status'] = status_value
            letter_dict.setdefault('statusStartDate', now)

            history = letter_dict.get('status_history')
            if not isinstance(history, list) or not history:
                letter_dict['status_history'] = [
                    {
                        'status': status_value,
                        'changed_at': now,
                        'actor_id': self._resolve_user_id(letter.created_by),
                    }
                ]

            result = await self.db.letters.insert_one(letter_dict)

            if not result.inserted_id:
                raise LetterServiceError("Failed to insert letter - no ID returned")

            created_letter_data = await self.db.letters.find_one({"_id": result.inserted_id})

            if not created_letter_data:
                raise LetterServiceError("Failed to retrieve created letter")

            prepared = self._augment_letter_document(created_letter_data)
            created_letter = Letter(**prepared)
            logger.info(f"Created letter: {result.inserted_id}")
            return created_letter

        except ValueError:
            raise
        except LetterServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to create letter: {e}")
            raise LetterServiceError(f"Letter creation failed: {str(e)}")

    async def update_letter(
        self,
        letter_id: str,
        update_payload: Any,
        current_user: Optional[Any] = None,
    ) -> Optional[Letter]:
        """Update an existing letter and emit notifications for key status changes."""
        try:
            letter_oid = self._validate_letter_id(letter_id)
            existing = await self.get_letter(letter_id)
            if not existing:
                logger.info(f"Letter not found for update: {letter_id}")
                return None

            if isinstance(update_payload, Letter):
                update_dict = update_payload.model_dump(
                    by_alias=True,
                    exclude_unset=True,
                    exclude={'_id'},
                )
            else:
                try:
                    if hasattr(update_payload, "model_dump"):
                        update_values = update_payload.model_dump(exclude_unset=True)
                    elif isinstance(update_payload, dict):
                        update_values = {k: v for k, v in update_payload.items() if v is not None}
                    else:
                        update_values = {}
                except Exception:
                    update_values = {}

                base = existing.model_dump(by_alias=True, exclude_unset=False)
                base.update(update_values)
                merged_letter = Letter(**base)
                update_dict = merged_letter.model_dump(
                    by_alias=True,
                    exclude_unset=True,
                    exclude={'_id'},
                )

            update_dict.pop("_id", None)
            update_dict.pop("id", None)

            if not update_dict:
                logger.warning(f"No fields to update for letter: {letter_id}")
                return existing

            now = datetime.now(timezone.utc)
            update_dict['updatedAt'] = now

            status_changed = False
            new_status = update_dict.get('status')
            if new_status and new_status != getattr(existing, 'status', None):
                status_changed = True
                update_dict['statusStartDate'] = now

            operations: Dict[str, Any] = {"$set": update_dict}
            if status_changed:
                operations.setdefault("$push", {})
                operations["$push"]["status_history"] = {
                    "status": new_status,
                    "changed_at": now,
                    "actor_id": self._resolve_user_id(current_user),
                }

            result = await self.db.letters.update_one({"_id": letter_oid}, operations)
            if result.matched_count == 0:
                logger.info(f"Letter not found for update: {letter_id}")
                return None

            if result.modified_count == 0:
                return existing

            updated = await self.get_letter(letter_id)
            if updated:
                actor_id = self._resolve_user_id(current_user)
                event = self._status_to_event(updated.status)
                if event:
                    await self._emit_letter_event(
                        updated,
                        event,
                        actor_id,
                        extra={"message": "Letter updated"},
                    )
            return updated

        except InvalidLetterIdError:
            raise
        except ValueError:
            raise
        except LetterServiceError:
            raise
        except Exception as exc:
            logger.error(f"Failed to update letter {letter_id}: {exc}")
            raise LetterServiceError(f"Letter update failed: {str(exc)}")

    async def get_context_documents(
        self,
        letter_id: str,
        current_user: Any,
    ) -> Dict[str, Any]:
        """Return stored context documents and associated metadata for a letter.

        Authorisation to view the LETTER does not authorise the documents.
        Letter numbers are global strings that collide across projects and
        organisations, a curated id list is not authority, and a document's
        publication state can turn adverse after it was curated. Every row
        served here therefore has to clear two INDEPENDENT gates before it is
        serialized:

        * the actor's canonical row visibility (`build_scope_query`), narrowed
          - not authorised - by this letter's own organisation and project;
        * canonical publication authority (`is_consumable`).

        `current_user` carries no default so a caller cannot omit the identity
        the result is bounded by.
        """
        letter = await self.get_letter(letter_id)
        if not letter:
            raise LetterNotFoundError(f"Letter not found: {letter_id}")

        # The letter's own scope narrows INSIDE entitlement. It is passed to
        # the canonical helper as a request-style filter, so a value outside
        # the actor's entitlement denies every row rather than granting one.
        letter_org = str(letter.organization_id) if letter.organization_id else None
        letter_project = str(letter.project_id) if letter.project_id else None

        context_ids = list(getattr(letter, "context_document_ids", []) or [])
        document_service = DocumentService(self.db)
        documents = await document_service.get_documents_by_ids_in_scope(
            context_ids,
            current_user,
            organization_id=letter_org,
            project_id=letter_project,
        )
        # `document_ids` echoes the stored selection, so it is narrowed to the
        # documents that survived both gates. An id alone still discloses that
        # a document exists.
        authorized_ids = [str(doc.id) for doc in documents]

        def _serialize(doc: Document) -> Dict[str, Any]:
            return {
                "id": str(doc.id),
                "letterNo": doc.letterNo,
                "subject": doc.subject,
                "uploadType": doc.uploadType,
                "date": doc.date.isoformat() if doc.date else None,
                "project_id": doc.project_id,
                "organization_id": doc.organization_id,
                "summary": doc.summary,
                "keywords": doc.keywords or [],
            }

        suggested_documents: List[Dict[str, Any]] = []
        if getattr(letter, "letter_no", None):
            candidates = await document_service.list_documents_by_letter_no(
                letter.letter_no,
                current_user,
                limit=12,
                organization_id=letter_org,
                project_id=letter_project,
            )
            for candidate in candidates:
                candidate_id = str(candidate.id)
                if candidate_id in context_ids:
                    continue
                suggested_documents.append(_serialize(candidate))

        return {
            "document_ids": authorized_ids,
            "documents": [_serialize(doc) for doc in documents],
            "suggested_documents": suggested_documents,
        }

    async def save_context_documents(
        self,
        letter_id: str,
        document_ids: List[str],
        current_user: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Persist curated context documents for a letter after validation."""
        letter = await self.get_letter(letter_id)
        if not letter:
            raise LetterNotFoundError(f"Letter not found: {letter_id}")

        deduped: List[str] = []
        seen: set[str] = set()
        for raw in document_ids or []:
            if raw is None:
                continue
            value = str(raw).strip()
            if not value or value in seen:
                continue
            deduped.append(value)
            seen.add(value)

        document_service = DocumentService(self.db)
        documents: List[Document] = []
        if deduped:
            try:
                documents = await document_service.get_documents_by_ids(deduped)
            except (DocumentServiceError, DocumentNotFoundError) as exc:
                raise LetterServiceError(str(exc)) from exc

            if len(documents) != len(deduped):
                missing = set(deduped) - {str(doc.id) for doc in documents}
                raise LetterServiceError(
                    f"Context documents not found: {', '.join(sorted(missing))}"
                )

            letter_org = str(letter.organization_id)
            letter_project = str(letter.project_id) if letter.project_id else None
            for doc in documents:
                if str(doc.organization_id) != letter_org:
                    raise LetterServiceError(
                        "Document organization does not match letter organization"
                    )
                if letter_project and doc.project_id and str(doc.project_id) != letter_project:
                    raise LetterServiceError(
                        "Document project does not match letter project"
                    )

        await self.db.letters.update_one(
            {"_id": self._validate_letter_id(letter_id)},
            {
                "$set": {
                    "context_document_ids": deduped,
                    "updatedAt": datetime.now(timezone.utc),
                }
            },
        )
        return await self.get_context_documents(letter_id, current_user)

    async def add_comment(self, letter_id: str, comment: str, user_id: Optional[str] = None) -> bool:
        """
        Add a comment to a letter.
        
        Args:
            letter_id: Letter ID string
            comment: Comment text to add
            user_id: Optional user ID who added the comment
            
        Returns:
            True if comment was added successfully
            
        Raises:
            LetterServiceError: If adding comment fails
            InvalidLetterIdError: If letter ID is invalid
        """
        try:
            letter_oid = self._validate_letter_id(letter_id)
            
            if not comment or not isinstance(comment, str):
                raise ValueError("Comment must be a non-empty string")
            
            # Create comment object with metadata
            comment_obj = {
                "text": comment.strip(),
                "timestamp": datetime.now(timezone.utc),
                "user_id": user_id
            }
            
            logger.info(f"Adding comment to letter: {letter_id}")
            
            result = await self.db.letters.update_one(
                {"_id": letter_oid},
                {"$push": {"comments": comment_obj}}
            )
            
            success = result.matched_count > 0
            if success:
                logger.info(f"Added comment to letter: {letter_id}")
                letter = await self.get_letter(letter_id)
                actor_id = self._resolve_user_id(user_id)
                await self._emit_letter_event(
                    letter,
                    NotificationType.COMMENT_ADDED,
                    actor_id,
                    extra={"message": comment.strip()},
                )
            else:
                logger.warning(f"Letter not found when adding comment: {letter_id}")
            
            return success
            
        except InvalidLetterIdError:
            raise
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to add comment to letter {letter_id}: {e}")
            raise LetterServiceError(f"Add comment failed: {str(e)}")
    
    async def delete_letter(self, letter_id: str) -> bool:
        """
        Delete a letter by ID, including its FalkorDB graph node.

        The drafting pipeline creates Falkor Letter nodes for letters with a
        letter number, so deletion must clean the graph too. The node is only
        removed when no other live letter or document still owns the same
        normalized letter code (Falkor nodes are keyed solely by normCode).

        Args:
            letter_id: Letter ID string

        Returns:
            True if letter was deleted, False if not found

        Raises:
            LetterServiceError: If deletion fails
            InvalidLetterIdError: If letter ID is invalid
        """
        try:
            letter_oid = self._validate_letter_id(letter_id)

            logger.info(f"Deleting letter: {letter_id}")

            letter_doc = await self.db.letters.find_one({"_id": letter_oid}) or {}
            result = await self.db.letters.delete_one({"_id": letter_oid})

            success = result.deleted_count > 0
            if success:
                logger.info(f"Deleted letter: {letter_id}")
                await self._cleanup_letter_graph_node(letter_oid, letter_doc)
            else:
                logger.info(f"Letter not found for deletion: {letter_id}")

            return success

        except InvalidLetterIdError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete letter {letter_id}: {e}")
            raise LetterServiceError(f"Letter deletion failed: {str(e)}")

    async def _cleanup_letter_graph_node(
        self,
        letter_oid: ObjectId,
        letter_doc: Dict[str, Any],
    ) -> None:
        """Best-effort FalkorDB cleanup after a letter hard delete.

        Failures are logged at WARNING and never mask the deletion itself —
        the graph cleanup is repairable, the delete is already committed.
        """
        try:
            from .falkor_graph_service import FalkorGraphService, normalize_letter_code

            letter_no = letter_doc.get("letter_no") or (
                (letter_doc.get("reference") or {}).get("reference_number")
                if isinstance(letter_doc.get("reference"), dict)
                else None
            )
            if not letter_no:
                return
            norm_code = normalize_letter_code(str(letter_no))
            if not norm_code:
                return

            other_letter = await self.db.letters.find_one(
                {"_id": {"$ne": letter_oid}, "letter_no": letter_no},
                {"_id": 1},
            )
            other_document = await self.db.documents.find_one(
                {
                    "letterNoNormalized": norm_code,
                    "lifecycle_state": {"$ne": "deleted"},
                },
                {"_id": 1},
            )
            if other_letter or other_document:
                logger.info(
                    "Skipping Falkor node deletion for letter %s: normCode %s "
                    "still owned by another live record",
                    letter_oid,
                    norm_code,
                )
                return

            FalkorGraphService().delete_letter(str(letter_no))
        except Exception as exc:
            logger.warning(
                "Falkor graph cleanup failed after deleting letter %s: %s",
                letter_oid,
                exc,
            )
    
    async def change_status(
        self,
        letter_id: str,
        new_status: str,
        comment: Optional[str] = None,
        user_id: Optional[str] = None,
        validate_transition: bool = False,
    ) -> bool:
        """
        Change the status of a letter.

        Args:
            letter_id: Letter ID string
            new_status: New status to set
            comment: Optional comment about the status change
            user_id: Optional user ID who changed the status
            validate_transition: When True, enforce the workflow state machine
                (``workflow_engine.can_transition``) against the letter's current
                status and reject invalid jumps. Off by default so system/compat
                callers (notifications, aliases) keep their existing behaviour.

        Returns:
            True if status was changed successfully

        Raises:
            LetterServiceError: If status change fails or the transition is invalid
            InvalidLetterIdError: If letter ID is invalid
        """
        try:
            letter_oid = self._validate_letter_id(letter_id)

            # Defense-in-depth: never write a non-string comment into Mongo. A
            # pydantic/user object here is both unencodable (BSON) and a data leak.
            if comment is not None and not isinstance(comment, str):
                logger.warning(
                    "change_status received a non-string comment (%s); coercing to str",
                    type(comment).__name__,
                )
                comment = None

            if validate_transition:
                current = await self.db.letters.find_one(
                    {'_id': letter_oid}, {'status': 1}
                )
                current_status = (current or {}).get('status')
                if current_status:
                    try:
                        allowed_transition = workflow_engine.can_transition(
                            str(current_status), str(new_status)
                        )
                    except Exception:
                        # Unrecognized legacy status -> cannot validate; allow and
                        # let the change proceed rather than block a real letter.
                        allowed_transition = True
                    if not allowed_transition:
                        allowed = workflow_engine.get_valid_transitions(str(current_status))
                        raise InvalidLetterTransitionError(
                            f"Invalid transition '{current_status}' -> '{new_status}'. Allowed: {allowed}"
                        )

            logger.info(f"Changing status of letter {letter_id} to {new_status}")

            now = datetime.now(timezone.utc)
            update_data = {
                'status': new_status,
                'updatedAt': now,
                'statusStartDate': now,
            }

            operations: Dict[str, Any] = {'$set': update_data}
            actor_id = self._resolve_user_id(user_id)
            history_entry: Dict[str, Any] = {
                'status': new_status,
                'changed_at': now,
                'actor_id': actor_id,
            }
            if comment:
                history_entry['comment'] = comment

            push_ops: Dict[str, Any] = {
                'status_history': history_entry
            }

            if comment:
                status_comment = {
                    'text': f"Status changed to {new_status}: {comment}",
                    'timestamp': now,
                    'user_id': actor_id,
                    'type': 'status_change'
                }
                push_ops['comments'] = status_comment

            operations['$push'] = push_ops

            result = await self.db.letters.update_one({'_id': letter_oid}, operations)

            success = result.matched_count > 0
            if success:
                logger.info(f"Changed status of letter {letter_id} to {new_status}")
                letter = await self.get_letter(letter_id)
                event = self._status_to_event(new_status)
                if event:
                    await self._emit_letter_event(
                        letter,
                        event,
                        actor_id,
                        extra={'message': comment} if comment else None,
                    )
                # Best-effort: keep the assignment board in step with the lifecycle.
                if letter is not None:
                    try:
                        from .task_sync_service import TaskSyncService

                        await TaskSyncService(self.db).on_letter_status_changed(letter, new_status, actor_id)
                    except Exception:
                        logger.debug("Task board sync skipped for letter %s", letter_id, exc_info=True)
            else:
                logger.warning(f"Letter not found when changing status: {letter_id}")

            return success

        except InvalidLetterTransitionError:
            raise
        except InvalidLetterIdError:
            raise
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to change status of letter {letter_id}: {e}")
            raise LetterServiceError(f"Status change failed: {str(e)}")

    async def request_input(
        self, 
        letter_id: str, 
        requested_from: str, 
        message: str,
        user_id: Optional[str] = None
    ) -> bool:
        """
        Request input from a user for a letter.
        
        Args:
            letter_id: Letter ID string
            requested_from: User ID or name to request input from
            message: Input request message
            user_id: Optional user ID who requested the input
            
        Returns:
            True if input request was recorded successfully
            
        Raises:
            LetterServiceError: If request fails
            InvalidLetterIdError: If letter ID is invalid
        """
        try:
            if not requested_from or not isinstance(requested_from, str):
                raise ValueError("requested_from must be a non-empty string")
            
            if not message or not isinstance(message, str):
                raise ValueError("message must be a non-empty string")
            
            # Create input request comment
            request_comment = f"Input requested from {requested_from}: {message}"
            
            logger.info(f"Recording input request for letter {letter_id}")
            
            # Add as special comment with metadata
            success = await self.add_comment(letter_id, request_comment, user_id)
            
            # TODO: In a real application, you might want to:
            # 1. Add this request to a separate "requests" collection
            # 2. Send notification to the requested user
            # 3. Track request status and responses
            
            if success:
                logger.info(f"Input request recorded for letter {letter_id}")
            
            return success
            
        except (InvalidLetterIdError, ValueError):
            raise
        except Exception as e:
            logger.error(f"Failed to request input for letter {letter_id}: {e}")
            raise LetterServiceError(f"Input request failed: {str(e)}")
    
    async def letter_exists(self, letter_id: str) -> bool:
        """
        Check if a letter exists.
        
        Args:
            letter_id: Letter ID string
            
        Returns:
            True if letter exists, False otherwise
            
        Raises:
            LetterServiceError: If check fails
            InvalidLetterIdError: If letter ID is invalid
        """
        try:
            letter_oid = self._validate_letter_id(letter_id)
            
            count = await self.db.letters.count_documents({"_id": letter_oid}, limit=1)
            return count > 0
            
        except InvalidLetterIdError:
            raise
        except Exception as e:
            logger.error(f"Failed to check letter existence {letter_id}: {e}")
            raise LetterServiceError(f"Letter existence check failed: {str(e)}")

    async def get_letter_by_id(self, letter_id: str) -> Optional[Letter]:
        """Compatibility alias used by routers."""
        return await self.get_letter(letter_id)

    def _status_to_event(self, status: Optional[str]) -> Optional[NotificationType]:
        if not status:
            return None
        normalized = status.lower()
        if normalized in {"input", "draft requested"}:
            return NotificationType.DRAFT_REQUESTED
        if normalized in {"draft", "review", "strategy"}:
            return NotificationType.DRAFT_SAVED
        if normalized in {"approval", "pending approval", "awaiting approval"}:
            return NotificationType.APPROVAL_ASSIGNED
        if normalized in {"approved", "completed"}:
            return NotificationType.APPROVAL_COMPLETED
        if normalized == "rejected":
            return NotificationType.APPROVAL_REJECTED
        return None

    @staticmethod
    def _resolve_user_id(user: Optional[Any]) -> Optional[str]:
        if not user:
            return None
        if isinstance(user, str):
            return user
        if isinstance(user, dict):
            for key in ("id", "_id", "email"):
                value = user.get(key)
                if value:
                    return str(value)
            return None
        for attr in ("id", "_id", "email"):
            value = getattr(user, attr, None)
            if value:
                return str(value)
        return None

    async def _emit_letter_event(
        self,
        letter: Optional[Letter],
        event: NotificationType,
        actor_id: Optional[str],
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.notification_service or not letter or not letter.id:
            return
        data = {
            "title": letter.title,
            "status": letter.status,
            "letter_id": str(letter.id),
        }
        if extra:
            data.update({k: v for k, v in extra.items() if v is not None})
        data.setdefault("message", self._workflow_message(letter, event, extra))
        include_users = []
        if getattr(letter, "assigned_to", None):
            include_users.append(str(letter.assigned_to))
        context = (
            NotificationContext.PROJECT
            if getattr(letter, "project_id", None)
            else NotificationContext.ORGANIZATION
        )
        await self.notification_service.emit(
            event,
            str(letter.id),
            "letter",
            context=context,
            actor_id=actor_id,
            data=data,
            include_users=include_users if include_users else None,
            actions=self._workflow_actions(str(letter.id), event),
            resource_link=self._workflow_link(str(letter.id), event),
        )

    @staticmethod
    def _workflow_link(letter_id: str, event: NotificationType) -> str:
        if event in {
            NotificationType.APPROVAL_ASSIGNED,
            NotificationType.APPROVAL_COMPLETED,
            NotificationType.APPROVAL_REJECTED,
        }:
            return f"/letters/{letter_id}/approval"
        if event == NotificationType.DRAFT_REQUESTED:
            return f"/letters/{letter_id}/draft"
        return f"/letters/{letter_id}/input"

    def _workflow_actions(self, letter_id: str, event: NotificationType) -> List[Dict[str, Any]]:
        view_label = "View Letter"
        link = self._workflow_link(letter_id, event)
        actions: List[Dict[str, Any]] = [
            {
                "key": "view_letter",
                "label": view_label,
                "method": "navigate",
                "href": link,
            }
        ]
        if event == NotificationType.APPROVAL_ASSIGNED:
            actions.extend(
                [
                    {
                        "key": "approve",
                        "label": "Approve",
                        "method": "post",
                        "href": None,
                    },
                    {
                        "key": "reject",
                        "label": "Reject",
                        "method": "post",
                        "href": None,
                    },
                ]
            )
        elif event == NotificationType.DRAFT_REQUESTED:
            actions.append(
                {
                    "key": "start_draft",
                    "label": "Start Draft",
                    "method": "post",
                    "href": None,
                }
            )
        elif event in {NotificationType.APPROVAL_COMPLETED, NotificationType.APPROVAL_REJECTED}:
            actions.append(
                {
                    "key": "mark_done",
                    "label": "Mark Done",
                    "method": "post",
                    "href": None,
                }
            )
        return actions

    @staticmethod
    def _workflow_message(
        letter: Letter,
        event: NotificationType,
        extra: Optional[Dict[str, Any]],
    ) -> str:
        comment = (extra or {}).get("message")
        if event == NotificationType.APPROVAL_ASSIGNED:
            return "Letter is waiting for approval"
        if event == NotificationType.APPROVAL_COMPLETED:
            return f"Letter approved{f': {comment}' if comment else ''}"
        if event == NotificationType.APPROVAL_REJECTED:
            return f"Letter rejected{f': {comment}' if comment else ''}"
        if event == NotificationType.DRAFT_REQUESTED:
            return "Draft work has been requested"
        return str(comment or f"Letter moved to {letter.status}")

    async def get_letters_paginated(self, authorized_query: Dict[str, Any], pagination: Dict[str, int]) -> List[Optional[Letter]]:
        """
        Retrieve letters respecting authorization filters and pagination.
        """
        filters = dict(authorized_query or {})
        skip = int(pagination.get('skip', 0) or 0)
        limit = int(pagination.get('limit', 50) or 50)

        search_query = filters.pop('search_query', None)
        tab_value = filters.pop('tab', None)
        if tab_value and 'status' not in filters:
            filters['status'] = tab_value

        return await self.get_letters(
            skip=skip,
            limit=limit,
            filters=filters,
            search_query=search_query,
        )

    async def create_letter_with_chain(self, letter_data: Any, conversation_context: Optional[Dict[str, Any]], user: Any) -> Letter:
        """
        Compatibility shim: construct a Letter from provided create data and delegate to create_letter.
        """
        try:
            data = letter_data.model_dump(exclude_unset=True) if hasattr(letter_data, "model_dump") else dict(letter_data or {})
        except Exception:
            data = {}

        new_letter = Letter(
            title=data.get("title", ""),
            recipient=data.get("recipient", ""),
            subject=data.get("subject", ""),
            content=data.get("content") or "",
            created_by=getattr(user, "id", None) or getattr(user, "email", "system"),
            assigned_to=data.get("assigned_to") or "",
            organization_id=data.get("organization_id") or "",
            project_id=data.get("project_id"),
            letter_no=data.get("letter_no"),
            date=data.get("date"),
            from_party_id=data.get("from_party_id"),
            to_party_id=data.get("to_party_id"),
            references=data.get("references") or [],
        )
        created = await self.create_letter(new_letter)
        await self._emit_letter_event(
            created,
            NotificationType.DRAFT_REQUESTED,
            actor_id=self._resolve_user_id(user),
            extra={"message": "Draft created"},
        )
        return created

    async def update_status(self, letter_id: str, new_status: str, user: Optional[Any] = None) -> bool:
        """Compatibility alias mapping to change_status."""
        return await self.change_status(letter_id, new_status, None, getattr(user, "id", None))

    async def record_langgraph_result(
        self,
        letter_id: str,
        graph_result: "LetterGraphResult",
        created_by: Optional[Any] = None,
    ) -> None:
        """Persist LangGraph output for the given letter."""
        letter_oid = self._validate_letter_id(letter_id)
        payload = graph_result.to_storage_dict()
        author_id = self._resolve_user_id(created_by)
        now = datetime.now(timezone.utc)

        existing_doc = await self.db.letters.find_one(
            {"_id": letter_oid},
            {
                "draft_versions": 1,
                "draft_output": 1,
                "draft_plan": 1,
                "current_draft_version": 1,
            },
        )
        existing_versions = (
            existing_doc.get("draft_versions", []) if existing_doc else []
        )
        last_version_number = 0
        for version in existing_versions:
            try:
                last_version_number = max(
                    last_version_number, int(version.get("version", 0))
                )
            except Exception:
                continue
        next_version = last_version_number + 1

        reviewer_blocking = bool(payload.get("reviewer_blocking"))
        is_analysis_only = graph_result.status == "analysis_only"

        base_doc: Dict[str, Any] = {
            "summary_points": payload.get("summary_points", []),
            "context_document_ids": payload.get("context_document_ids", []),
            "context_documents": payload.get("context_documents", []),
            "background_summary": payload.get("background_summary", []),
            "graph_thread": payload.get("graph_thread", []),
            "draft_sources": payload.get("draft_sources", []),
            "reviewer_findings": payload.get("reviewer_findings", []),
            "reviewer_blocking": reviewer_blocking,
            "tone_approach": payload.get("strategy_outline", {}).get("tone_approach"),
            "content_structure": payload.get("strategy_outline", {}).get("content_structure"),
            "specific_responses": payload.get("strategy_outline", {}).get("specific_responses"),
            "risk_mitigation": payload.get("strategy_outline", {}).get("risk_mitigation"),
            "desired_outcome": payload.get("strategy_outline", {}).get("desired_outcome"),
            "requirements_text": payload.get("requirements_text"),
            "plan_context_text": payload.get("plan_context_text"),
            "updated_at": now,
        }

        update_doc: Dict[str, Any]
        if is_analysis_only:
            update_doc: Dict[str, Any] = {
                **base_doc,
                "strategy_plan": payload.get("draft_plan"),
                "strategic_outline": payload.get("strategy_outline"),
                "strategy_run_id": payload.get("run_id"),
                "strategy_graph_status": payload.get("graph_status"),
                "strategy_graph_started_at": payload.get("graph_started_at"),
                "strategy_graph_completed_at": payload.get("graph_completed_at"),
                "strategy_graph_trace": payload.get("draft_trace", []),
            }
        else:
            update_doc = {
                **base_doc,
                "graph_status": payload.get("graph_status"),
                "graph_started_at": payload.get("graph_started_at"),
                "graph_completed_at": payload.get("graph_completed_at"),
                "graph_run_id": payload.get("run_id"),
                "graph_warnings": payload.get("graph_warnings", []),
                "draft_plan": payload.get("draft_plan"),
            }
            draft_trace = payload.get("draft_trace")
            if draft_trace is not None:
                update_doc["draft_trace"] = draft_trace
            if not reviewer_blocking:
                draft_output = payload.get("draft_output")
                if draft_output is not None:
                    update_doc["draft_output"] = draft_output
                if payload.get("draft_plan") is not None:
                    update_doc["draft_plan"] = payload.get("draft_plan")
                update_doc["current_draft_version"] = next_version

        version_entry: Optional[Dict[str, Any]] = None
        if not is_analysis_only:
            version_entry = {
                "version": next_version,
                "status": graph_result.status,
                "body": payload.get("draft_output") or graph_result.draft.body,
                "plan": payload.get("draft_plan") or graph_result.plan,
                "sources": payload.get("draft_sources", []),
                "reviewer_findings": payload.get("reviewer_findings", []),
                "run_id": payload.get("run_id"),
                "created_at": now,
                "created_by": author_id,
            }

        operations: Dict[str, Any] = {"$set": update_doc}
        if version_entry:
            operations.setdefault("$push", {})
            operations["$push"]["draft_versions"] = version_entry

        await self.db.letters.update_one({"_id": letter_oid}, operations)

    async def get_langgraph_snapshot(self, letter_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest LangGraph run snapshot for the letter."""
        letter = await self.get_letter(letter_id)
        if not letter or not getattr(letter, "graph_status", None):
            return None

        def _stringify(value: Any) -> Any:
            if isinstance(value, ObjectId):
                return str(value)
            if isinstance(value, list):
                return [_stringify(item) for item in value]
            if isinstance(value, dict):
                return {k: _stringify(v) for k, v in value.items()}
            return value

        raw_trace = getattr(letter, "draft_trace", []) or []
        node_traces: List[LangGraphNodeTrace] = []
        for raw in raw_trace:
            if isinstance(raw, LangGraphNodeTrace):
                node_traces.append(raw)
            elif isinstance(raw, dict):
                try:
                    node_traces.append(LangGraphNodeTrace(**raw))
                except Exception:
                    continue

        draft = LetterDraftResponse(
            subject=letter.subject,
            body=getattr(letter, "draft_output", "") or "",
            key_points=getattr(letter, "summary_points", []) or [],
        )
        started_at = getattr(letter, "graph_started_at", datetime.now(timezone.utc))
        completed_at = getattr(letter, "graph_completed_at", datetime.now(timezone.utc))

        response = LangGraphDraftResponse(
            letter_id=letter_id,
            run_id=getattr(letter, "graph_run_id", "") or "",
            status=getattr(letter, "graph_status", "") or "unknown",
            plan=getattr(letter, "draft_plan", "") or "",
            draft=draft,
            warnings=getattr(letter, "graph_warnings", []) or [],
            trace=node_traces,
            summary_points=getattr(letter, "summary_points", []) or [],
            context_document_ids=_stringify(getattr(letter, "context_document_ids", []) or []),
            context_documents=_stringify(getattr(letter, "context_documents", []) or []),
            background_summary=_stringify(getattr(letter, "background_summary", []) or []),
            graph_thread=_stringify(getattr(letter, "graph_thread", []) or []),
            sources=_stringify(getattr(letter, "draft_sources", []) or []),
            reviewer_findings=_stringify(getattr(letter, "reviewer_findings", []) or []),
            reviewer_blocking=bool(getattr(letter, "reviewer_blocking", False)),
            tone_approach=_stringify(getattr(letter, "tone_approach", None)),
            content_structure=_stringify(getattr(letter, "content_structure", None)),
            specific_responses=_stringify(getattr(letter, "specific_responses", []) or []),
            risk_mitigation=_stringify(getattr(letter, "risk_mitigation", None)),
            desired_outcome=_stringify(getattr(letter, "desired_outcome", None)),
            requirements_text=getattr(letter, "requirements_text", None),
            context_text=getattr(letter, "plan_context_text", None),
            started_at=started_at,
            completed_at=completed_at,
        )
        return response.model_dump()

# Factory function
def create_letter_service(db: Database) -> LetterService:
    """Create letter service instance"""
    return LetterService(db)
