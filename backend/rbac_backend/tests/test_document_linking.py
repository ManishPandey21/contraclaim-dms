from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import status

from rbac_backend.core.security import CurrentUser
from rbac_backend.models.document import LinkDocumentsRequest
from rbac_backend.routers.documents import (
    DocumentController,
    controller_link_documents,
    controller_list_linked_documents,
)
from rbac_backend.utils.error_handler import DocumentError


@dataclass
class _StubDocument:
    id: str
    organization_id: str = "org-1"
    project_id: str = "proj-1"


class _StubDocumentService:
    def __init__(self) -> None:
        self.documents: Dict[str, _StubDocument] = {}
        self.link_calls: List[Dict[str, Any]] = []
        self.linked_payload: Dict[str, List[Dict[str, Any]]] = {}

    async def get_document_by_id(self, document_id: str) -> Optional[_StubDocument]:
        return self.documents.get(document_id)

    async def link_documents(
        self,
        source_document_id: str,
        target_document_id: str,
        link_type: str,
        *,
        description: Optional[str] = None,
        current_user: Any = None,
    ) -> Dict[str, Any]:
        self.link_calls.append(
            {
                "source_document_id": source_document_id,
                "target_document_id": target_document_id,
                "link_type": link_type,
                "description": description,
                "current_user_id": getattr(current_user, "id", None),
            }
        )
        self.linked_payload.setdefault(source_document_id, []).append(
            {
                "documentId": target_document_id,
                "linkType": link_type,
                "description": description,
            }
        )
        return {
            "message": "Documents linked successfully",
            "linked_documents": [item["documentId"] for item in self.linked_payload[source_document_id]],
        }

    async def list_linked_documents(self, document_id: str) -> List[Dict[str, Any]]:
        return list(self.linked_payload.get(document_id, []))


class _StubAuthService:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def check_document_access(
        self,
        current_user: CurrentUser,
        organization_id: str,
        project_id: str,
        permission: str,
    ) -> None:
        self.calls.append(
            {
                "user_id": current_user.id,
                "organization_id": organization_id,
                "project_id": project_id,
                "permission": permission,
            }
        )


def _make_controller() -> tuple[DocumentController, _StubDocumentService, _StubAuthService]:
    document_service = _StubDocumentService()
    auth_service = _StubAuthService()
    controller = DocumentController(
        document_service=document_service,  # type: ignore[arg-type]
        file_service=SimpleNamespace(),
        export_service=SimpleNamespace(),
        auth_service=auth_service,  # type: ignore[arg-type]
        bulk_upload_service=SimpleNamespace(),
    )
    return controller, document_service, auth_service


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


async def test_controller_link_documents_uses_current_payload_schema() -> None:
    controller, document_service, auth_service = _make_controller()
    user = _make_user()

    document_service.documents["doc-a"] = _StubDocument(id="doc-a")
    document_service.documents["doc-b"] = _StubDocument(id="doc-b")

    result = await controller_link_documents(
        controller,
        LinkDocumentsRequest(
            source_document_id="doc-a",
            target_document_id="doc-b",
            link_type="direct",
            description="Related workstream",
        ),
        user,
    )

    assert result == {
        "message": "Documents linked successfully",
        "linked_documents": ["doc-b"],
    }
    assert document_service.link_calls == [
        {
            "source_document_id": "doc-a",
            "target_document_id": "doc-b",
            "link_type": "direct",
            "description": "Related workstream",
            "current_user_id": "user-123",
        }
    ]
    assert auth_service.calls == [
        {
            "user_id": "user-123",
            "organization_id": "org-1",
            "project_id": "proj-1",
            "permission": "update",
        },
        {
            "user_id": "user-123",
            "organization_id": "org-1",
            "project_id": "proj-1",
            "permission": "read",
        },
    ]


async def test_controller_list_linked_documents_returns_service_payload() -> None:
    controller, document_service, auth_service = _make_controller()
    user = _make_user()

    document_service.documents["doc-a"] = _StubDocument(id="doc-a")
    document_service.linked_payload["doc-a"] = [
        {
            "documentId": "doc-b",
            "linkType": "direct",
            "description": "Related workstream",
        }
    ]

    result = await controller_list_linked_documents(controller, "doc-a", user)

    assert result == [
        {
            "documentId": "doc-b",
            "linkType": "direct",
            "description": "Related workstream",
        }
    ]
    assert auth_service.calls == [
        {
            "user_id": "user-123",
            "organization_id": "org-1",
            "project_id": "proj-1",
            "permission": "read",
        }
    ]


async def test_controller_link_documents_raises_not_found_for_missing_documents() -> None:
    controller, document_service, _auth_service = _make_controller()
    user = _make_user()

    document_service.documents["doc-a"] = _StubDocument(id="doc-a")

    with pytest.raises(DocumentError) as excinfo:
        await controller_link_documents(
            controller,
            LinkDocumentsRequest(
                source_document_id="doc-a",
                target_document_id="missing-doc",
                link_type="direct",
            ),
            user,
        )

    assert excinfo.value.http_status == status.HTTP_404_NOT_FOUND
    assert "Document not found" in str(excinfo.value)
