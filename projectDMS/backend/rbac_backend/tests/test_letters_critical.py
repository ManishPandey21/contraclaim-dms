from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Dict, List

import httpx
import pytest
from fastapi import HTTPException

from rbac_backend.core.security import CurrentUser
from rbac_backend.main import app
from rbac_backend.models.ai_models import LangGraphDraftRequest
from rbac_backend.models.input_request import InputRequest, InputRequestCreate
from rbac_backend.models.letter import ConversationTree, Letter, LetterCreate, LetterUpdate
from rbac_backend.routers.ai_assistant import AIAssistantController
from rbac_backend.routers.letters import LetterController, get_letter_controller
from rbac_backend.routers.input_requests import InputRequestController
from rbac_backend.utils.error_handler import AuthorizationError


def _json_id(payload: dict) -> str:
    return payload.get("_id") or payload.get("id")


def _make_user() -> CurrentUser:
    return CurrentUser(
        id="user-123",
        username="tester",
        email="tester@example.com",
        roles=["superadmin"],
        organizations=[],
        projects=[],
        disabled=False,
    )


class _FakeInputRequestService:
    async def create_request(self, letter_id: str, data: InputRequestCreate, current_user: CurrentUser) -> InputRequest:
        return InputRequest(
            _id="507f1f77bcf86cd799439013",
            letter_id=letter_id,
            requested_by=current_user.id,
            requested_from=data.requested_from,
            details=data.details,
            due_date=data.due_date,
        )


class _FakeLettersCollection:
    def __init__(self) -> None:
        self.last_filter = None
        self.last_update = None

    async def update_one(self, filter_doc, update_doc):
        self.last_filter = filter_doc
        self.last_update = update_doc
        return SimpleNamespace(matched_count=1)


class _FakeLetterServiceForInput:
    def __init__(self, letter: Letter) -> None:
        self.letter = letter
        self.db = SimpleNamespace(letters=_FakeLettersCollection())

    async def get_letter_by_id(self, _letter_id: str) -> Letter:
        return self.letter


class _FakeAuthService:
    async def require_permission(self, *_args, **_kwargs) -> None:
        return None

    async def check_letter_access(self, *_args, **_kwargs) -> None:
        return None


class _RejectingAuthService(_FakeAuthService):
    async def require_permission(self, *_args, **_kwargs) -> None:
        raise AuthorizationError("Forbidden")


class _FakeRateLimiter:
    async def check_user_limit(self, *_args, **_kwargs) -> None:
        return None


class _FakeAuditLogger:
    async def log_input_request_created(self, *_args, **_kwargs) -> None:
        return None


class _FakeUsersCollection:
    def __init__(self) -> None:
        self.users = [
            {"_id": "provider-user", "username": "provider", "email": "provider@example.com"},
            {"_id": "drafter-user", "username": "drafter", "email": "drafter@example.com"},
        ]

    async def find_one(self, query):
        candidates = query.get("$or", [query])
        for candidate in candidates:
            key, expected = next(iter(candidate.items()))
            for user in self.users:
                if str(user.get(key)) == str(expected):
                    return user
        return None


class _FakeEmailService:
    app_url = "http://localhost:5173"

    def __init__(self) -> None:
        self.sent = None

    async def send_share_email(self, **kwargs) -> bool:
        self.sent = kwargs
        return True


@pytest.mark.asyncio
async def test_letter_update_validator_preserves_strategy_plan_and_draft_status():
    controller = LetterController(None, None, None, None)

    validated = await controller._validate_letter_update(
        LetterUpdate(
            strategy_plan="Strategic roadmap",
            strategic_outline={"recommended_response_strategy": "Protect entitlement"},
            summary_points=["Verify clause 20.1"],
            strategy_role="contractor",
            status="Draft",
        )
    )

    assert validated.strategy_plan == "Strategic roadmap"
    assert validated.strategic_outline == {
        "recommended_response_strategy": "Protect entitlement"
    }
    assert validated.summary_points == ["Verify clause 20.1"]
    assert validated.strategy_role == "contractor"
    assert validated.status == "Draft"


@dataclass
class _StoredLetter:
    model: Letter


@pytest.mark.asyncio
async def test_input_request_creation_moves_letter_to_input_and_emails_drafter_contact():
    letter_id = "507f1f77bcf86cd799439011"
    letter = Letter(
        _id=letter_id,
        title="Delay Notice",
        recipient="Engineer",
        subject="Extension of time",
        content="Incoming request",
        created_by="creator-user",
        assigned_to="drafter-user",
        organization_id="org1",
        project_id="proj1",
    )
    letter_service = _FakeLetterServiceForInput(letter)
    email_service = _FakeEmailService()
    notification_service = SimpleNamespace(
        db=SimpleNamespace(users=_FakeUsersCollection()),
        email_service=email_service,
    )
    controller = InputRequestController(
        _FakeInputRequestService(),
        letter_service,
        _FakeAuthService(),
        _FakeRateLimiter(),
        _FakeAuditLogger(),
        notification_service,
    )

    request = await controller.create_input_request(
        letter_id,
        InputRequestCreate(
            requested_from="provider-user",
            details="Confirm the actual delay events and supporting documents.",
        ),
        _make_user(),
    )

    assert request.id == "507f1f77bcf86cd799439013"
    assert letter_service.db.letters.last_update["$set"]["status"] == "Input"
    assert "status_history" in letter_service.db.letters.last_update["$push"]
    assert email_service.sent["to"] == ["provider@example.com"]
    assert "Confirm the actual delay events" in email_service.sent["text_body"]
    assert "drafter@example.com" in email_service.sent["text_body"]


@pytest.mark.asyncio
async def test_input_request_creation_preserves_authorization_errors():
    letter_id = "507f1f77bcf86cd799439011"
    letter = Letter(
        _id=letter_id,
        title="Delay Notice",
        recipient="Engineer",
        subject="Extension of time",
        content="Incoming request",
        created_by="creator-user",
        assigned_to="drafter-user",
        organization_id="org1",
        project_id="proj1",
    )
    controller = InputRequestController(
        _FakeInputRequestService(),
        _FakeLetterServiceForInput(letter),
        _RejectingAuthService(),
        _FakeRateLimiter(),
        _FakeAuditLogger(),
    )

    with pytest.raises(AuthorizationError):
        await controller.create_input_request(
            letter_id,
            InputRequestCreate(
                requested_from="provider-user",
                details="Confirm the actual delay events and supporting documents.",
            ),
            _make_user(),
        )


class _FakeConversationService:
    def __init__(self, store: Dict[str, _StoredLetter]) -> None:
        self._store = store

    async def build_conversation_tree(self, letter_id: str, _current_user: CurrentUser) -> ConversationTree:
        root = self._store[letter_id].model

        def _build(node: Letter) -> ConversationTree:
            children = [
                _build(item.model)
                for item in self._store.values()
                if item.model.previous_letter_id == node.id
            ]
            return ConversationTree(letter=node, children=children)

        return _build(root)

    async def get_conversation_chain(self, letter_id: str, _current_user: CurrentUser) -> List[Letter]:
        current = self._store[letter_id].model
        conversation_id = current.conversation_id
        members = [
            item.model
            for item in self._store.values()
            if item.model.conversation_id == conversation_id
        ]
        return sorted(members, key=lambda item: (item.depth, item.created_at))

    async def reparent_letter_atomic(self, letter_id: str, new_parent_id: str, _current_user: CurrentUser) -> None:
        letter = self._store[letter_id].model
        new_parent = self._store[new_parent_id].model

        if letter_id == new_parent_id or letter_id in (new_parent.ancestors or []):
            raise HTTPException(status_code=400, detail="Cycle detected in reparenting")

        updated = letter.model_copy(
            update={
                "previous_letter_id": new_parent.id,
                "conversation_id": new_parent.conversation_id,
                "ancestors": (new_parent.ancestors or []) + [new_parent.id],
                "depth": new_parent.depth + 1,
            }
        )
        self._store[letter_id] = _StoredLetter(updated)


class _FakeLetterController:
    def __init__(self) -> None:
        self._store: Dict[str, _StoredLetter] = {}
        self.conversation_service = _FakeConversationService(self._store)
        self._sequence = 0

    async def create_letter(self, letter_data: LetterCreate, current_user: CurrentUser) -> Letter:
        self._sequence += 1
        letter_id = f"letter-{self._sequence}"
        parent = None
        if letter_data.previous_letter_id:
            parent = self._store[letter_data.previous_letter_id].model

        conversation_id = parent.conversation_id if parent else letter_id
        ancestors = (parent.ancestors or []) + [parent.id] if parent else []
        depth = len(ancestors)

        letter = Letter(
            _id=letter_id,
            title=letter_data.title,
            recipient=letter_data.recipient,
            subject=letter_data.subject,
            content=letter_data.content or "",
            created_by=current_user.id,
            assigned_to=letter_data.assigned_to,
            organization_id=letter_data.organization_id or "org1",
            project_id=letter_data.project_id or "proj1",
            conversation_id=conversation_id,
            previous_letter_id=letter_data.previous_letter_id,
            ancestors=ancestors,
            depth=depth,
        )
        self._store[letter_id] = _StoredLetter(letter)
        return letter

    async def get_conversation_chain(self, letter_id: str, current_user: CurrentUser) -> List[Letter]:
        return await self.conversation_service.get_conversation_chain(letter_id, current_user)


@pytest.fixture
def fake_letter_controller():
    controller = _FakeLetterController()

    async def _override_controller():
        return controller

    def _override_user():
        return _make_user()

    app.dependency_overrides[get_letter_controller] = _override_controller
    from rbac_backend.routers.letters import get_current_user as letters_get_current_user

    app.dependency_overrides[letters_get_current_user] = _override_user
    try:
        yield controller
    finally:
        app.dependency_overrides.pop(get_letter_controller, None)
        app.dependency_overrides.pop(letters_get_current_user, None)


async def test_create_root_letter(fake_letter_controller) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/letters",
            json={
                "title": "Root Letter",
                "recipient": "Recipient A",
                "subject": "Root Subject",
                "assigned_to": "user1",
                "organization_id": "org1",
                "project_id": "proj1",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert _json_id(data) == "letter-1"
    assert data["conversation_id"] == "letter-1"
    assert data.get("previous_letter_id") is None
    assert data.get("ancestors") == []
    assert data.get("depth") == 0


async def test_create_reply_letter(fake_letter_controller) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root_response = await client.post(
            "/api/letters",
            json={
                "title": "Root Letter",
                "recipient": "Recipient A",
                "subject": "Root Subject",
                "assigned_to": "user1",
                "organization_id": "org1",
                "project_id": "proj1",
            },
        )
        root_data = root_response.json()
        root_id = _json_id(root_data)

        reply_response = await client.post(
            "/api/letters",
            json={
                "title": "Reply Letter",
                "recipient": "Recipient B",
                "subject": "Reply Subject",
                "assigned_to": "user2",
                "organization_id": "org1",
                "project_id": "proj1",
                "previous_letter_id": root_id,
            },
        )

    assert reply_response.status_code == 200
    reply_data = reply_response.json()
    assert reply_data["conversation_id"] == root_data["conversation_id"]
    assert reply_data["previous_letter_id"] == root_id
    assert reply_data["depth"] == 1
    assert root_id in reply_data["ancestors"]


async def test_reparent_cycle_prevention(fake_letter_controller) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root_response = await client.post(
            "/api/letters",
            json={
                "title": "Root Letter",
                "recipient": "Recipient A",
                "subject": "Root Subject",
                "assigned_to": "user1",
                "organization_id": "org1",
                "project_id": "proj1",
            },
        )
        root_id = _json_id(root_response.json())

        reply_response = await client.post(
            "/api/letters",
            json={
                "title": "Reply Letter",
                "recipient": "Recipient B",
                "subject": "Reply Subject",
                "assigned_to": "user2",
                "organization_id": "org1",
                "project_id": "proj1",
                "previous_letter_id": root_id,
            },
        )
        reply_id = _json_id(reply_response.json())

        response = await client.post(
            f"/api/letters/{root_id}/reparent",
            json={"new_parent_id": reply_id},
        )

    assert response.status_code == 400
    assert "Cycle detected" in response.json()["detail"]


async def test_get_conversation_tree(fake_letter_controller) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root_response = await client.post(
            "/api/letters",
            json={
                "title": "Root Letter",
                "recipient": "Recipient A",
                "subject": "Root Subject",
                "assigned_to": "user1",
                "organization_id": "org1",
                "project_id": "proj1",
            },
        )
        root_id = _json_id(root_response.json())

        reply_response = await client.post(
            "/api/letters",
            json={
                "title": "Reply Letter",
                "recipient": "Recipient B",
                "subject": "Reply Subject",
                "assigned_to": "user2",
                "organization_id": "org1",
                "project_id": "proj1",
                "previous_letter_id": root_id,
            },
        )
        reply_id = _json_id(reply_response.json())

        tree_response = await client.get(f"/api/letters/{root_id}/tree")

    assert tree_response.status_code == 200
    tree_data = tree_response.json()
    assert _json_id(tree_data["letter"]) == root_id
    assert len(tree_data["children"]) == 1
    assert _json_id(tree_data["children"][0]["letter"]) == reply_id


async def test_get_conversation_chain(fake_letter_controller) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        root_response = await client.post(
            "/api/letters",
            json={
                "title": "Root Letter",
                "recipient": "Recipient A",
                "subject": "Root Subject",
                "assigned_to": "user1",
                "organization_id": "org1",
                "project_id": "proj1",
            },
        )
        root_id = _json_id(root_response.json())

        reply_response = await client.post(
            "/api/letters",
            json={
                "title": "Reply Letter",
                "recipient": "Recipient B",
                "subject": "Reply Subject",
                "assigned_to": "user2",
                "organization_id": "org1",
                "project_id": "proj1",
                "previous_letter_id": root_id,
            },
        )
        reply_id = _json_id(reply_response.json())

        chain_response = await client.get(f"/api/letters/{reply_id}/chain")

    assert chain_response.status_code == 200
    chain = chain_response.json()
    assert [_json_id(item) for item in chain] == [root_id, reply_id]


async def test_langgraph_sanitize_preserves_letter_id() -> None:
    controller = AIAssistantController(
        ai_service=SimpleNamespace(),
        cache_service=SimpleNamespace(),
        rate_limiter=SimpleNamespace(),
        llm_config_service=SimpleNamespace(),
    )
    request = LangGraphDraftRequest(
        letter_id="letter-123",
        subject="LangGraph Test",
        recipient="Reviewer",
    )

    sanitized = await controller._sanitize_draft_request(request)

    assert isinstance(sanitized, LangGraphDraftRequest)
    assert sanitized.letter_id == "letter-123"


async def test_langgraph_sanitize_preserves_analysis_only() -> None:
    controller = AIAssistantController(
        ai_service=SimpleNamespace(),
        cache_service=SimpleNamespace(),
        rate_limiter=SimpleNamespace(),
        llm_config_service=SimpleNamespace(),
    )
    request = LangGraphDraftRequest(
        letter_id="letter-123",
        subject="LangGraph Test",
        recipient="Reviewer",
        analysis_only=True,
    )

    sanitized = await controller._sanitize_draft_request(request)

    assert isinstance(sanitized, LangGraphDraftRequest)
    assert sanitized.analysis_only is True
