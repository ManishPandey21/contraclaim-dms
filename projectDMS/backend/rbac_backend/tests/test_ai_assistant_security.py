import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from rbac_backend.core.permissions import Permissions
from rbac_backend.models.ai_models import LetterDraftRequest
from rbac_backend.routers.ai_assistant import AIAssistantController, _require_platform_admin
from rbac_backend.services.ai_service import AIService


class _User:
    def __init__(self, roles):
        self.roles = roles


class _PolicyRecorder:
    def __init__(self):
        self.calls = []

    async def authorize(self, current_user, permission, **kwargs):
        self.calls.append(
            {
                "user": current_user,
                "permission": permission,
                **kwargs,
            }
        )


class _FakeLetterCursor:
    def __init__(self, docs=None):
        self.docs = docs or []

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self, length=None):
        return list(self.docs)


class _CapturingLetterCollection:
    def __init__(self):
        self.query = None

    def find(self, query):
        self.query = query
        return _FakeLetterCursor()


def test_ai_admin_guard_allows_superadmin():
    _require_platform_admin(_User(["superadmin"]))


def test_ai_admin_guard_rejects_non_superadmin():
    with pytest.raises(HTTPException) as exc:
        _require_platform_admin(_User(["orgadmin"]))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_ai_action_authorization_uses_policy_service_scope_and_permission():
    policy = _PolicyRecorder()
    controller = AIAssistantController(
        ai_service=SimpleNamespace(),
        cache_service=SimpleNamespace(),
        rate_limiter=SimpleNamespace(),
        llm_config_service=SimpleNamespace(),
        policy_service=policy,
    )
    user = SimpleNamespace(
        id="user-1",
        roles=["orguser"],
        organization_id="org-user-default",
        projects=["project-user-default"],
    )
    request = LetterDraftRequest(
        subject="Delay notice",
        recipient="Engineer",
        organization_id="org-request",
        project_id="project-request",
    )

    resolved_scope = await controller._authorize_ai_action(
        request,
        user,
        Permissions.DRAFTING_REQUEST_CREATE,
        resource_type="ai_assistant_draft",
        resource_id="letter-1",
    )

    assert resolved_scope == ("org-request", "project-request")
    assert policy.calls == [
        {
            "user": user,
            "permission": Permissions.DRAFTING_REQUEST_CREATE,
            "resource_type": "ai_assistant_draft",
            "resource_id": "letter-1",
            "organization_id": "org-request",
            "project_id": "project-request",
            "meter_event_type": None,
            "meter_quantity": 1,
            "meter_metadata": None,
            "audit": True,
        }
    ]


@pytest.mark.asyncio
async def test_ai_letter_search_service_filters_query_by_authorized_scope():
    collection = _CapturingLetterCollection()
    service = AIService()
    service._letters = collection

    await service.search_similar_letters(
        "delay",
        SimpleNamespace(id="user-1"),
        5,
        organization_id="org-request",
        project_id="project-request",
    )

    assert collection.query == {
        "$and": [
            {
                "$or": [
                    {"subject": {"$regex": "delay", "$options": "i"}},
                    {"content": {"$regex": "delay", "$options": "i"}},
                    {"summary": {"$regex": "delay", "$options": "i"}},
                    {"keywords": {"$regex": "delay", "$options": "i"}},
                ]
            },
            {"organization_id": {"$in": ["org-request"]}},
            {"project_id": {"$in": ["project-request"]}},
        ]
    }
