import asyncio
from typing import Any, Dict, List, Optional
from fastapi.testclient import TestClient

import rbac_backend.routers.deep_planning as dp
from rbac_backend.main import app
from rbac_backend.core.security import CurrentUser


class _FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    async def to_list(self, length: Optional[int] = None):
        return self._rows[: (length or len(self._rows))]


class _FakeFind:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def find(self, _filter: Dict[str, Any]):
        return _FakeCursor(self._rows)


class _FakeDocs:
    def __init__(self, store: Dict[str, Dict[str, Any]]):
        self._store = store

    async def find_one(self, filt: Dict[str, Any]):
        _id = filt.get("_id")
        return self._store.get(_id)

    def find(self, _filter: Dict[str, Any]):
        # Not used for documents in our flow
        return _FakeCursor([])


class _FakeLetters:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def find(self, _filter: Dict[str, Any]):
        return _FakeCursor(self._rows)

    async def update_one(self, *_args, **_kwargs):
        return {"acknowledged": True, "matched_count": 1, "modified_count": 1}


class _FakeDB:
    def __init__(self, documents_store: Dict[str, Dict[str, Any]], letters_rows: List[Dict[str, Any]]):
        self.documents = _FakeDocs(documents_store)
        self.letters = _FakeLetters(letters_rows)


def _make_fake_chat_response(text: str):
    class _Msg:
        def __init__(self, content: str):
            self.content = content

    class _Choice:
        def __init__(self, content: str):
            self.message = _Msg(content)

    class _Resp:
        def __init__(self, content: str):
            self.choices = [_Choice(content)]

    return _Resp(text)


def test_deep_planning_generate_draft_endpoint(monkeypatch):
    """
    End-to-end API test (with dependency overrides and OpenAI monkeypatch):
    - Overrides get_db and get_current_user.
    - Mocks embeddings and chat completion.
    - Confirms response model fields and plain-text draft.
    """
    # Fake DB with two documents and no existing letters (to keep find_similar_letters fast)
    docs_store = {
        # Strings used in router will be cast to ObjectId; we keep keys as strings to match access
        # The function passes ObjectId(doc_id) and uses it as key.
        # We can't build ObjectId keys easily here without string conversion inside fake. Simplify by accepting any ID:
    }
    # Accept any document id as not found by default
    fake_db = _FakeDB(documents_store=docs_store, letters_rows=[])

    async def fake_get_db():
        yield fake_db

    # Override dependencies
    app.dependency_overrides[dp.get_db] = fake_get_db

    def fake_current_user():
        return CurrentUser(
            id="test-user",
            username="tester",
            email="tester@example.com",
            roles=["superadmin"],
            organization_id=None,
            organizations=[],
            projects=[],
            disabled=False,
        )

    app.dependency_overrides[dp.get_current_user] = fake_current_user

    # Monkeypatch OpenAI calls used in the router
    def fake_embed_create(model: str, input: str):
        class _Data:
            def __init__(self):
                self.embedding = [0.1, 0.2, 0.3]
        class _Item:
            def __init__(self):
                self.data = [_Data()]
        return _Item()

    monkeypatch.setattr(dp.client.embeddings, "create", fake_embed_create)

    def fake_chat_create(model: str, messages: List[Dict[str, str]], max_tokens: int, temperature: float):
        # Return a simple, plain-text draft content
        return _make_fake_chat_response(
            "Date: 2025-09-08\n\nSubject: Test Subject\n\nDear Sir/Madam,\n\nPurpose...\nFacts...\nPosition...\nAction...\nClosing...\n\nYours faithfully,\n"
        )

    monkeypatch.setattr(dp.client.chat.completions, "create", fake_chat_create)

    client = TestClient(app)

    payload = {
        "document_ids": [],  # empty is allowed; function handles gracefully
        "subject": "Test Subject",
        "recipient": "Owner",
        "user_id": "test-user",
        "context": "Key context line 1",
        "points": "Point A\nPoint B",
        "target_letter_id": None,
        "organization_id": None,
        "project_id": None
    }

    resp = client.post("/api/deep-planning/generate-draft", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Response model checks
    assert "draft_letter" in data
    assert isinstance(data["draft_letter"], str)
    # Ensure plain text (no markdown tokens)
    assert "<" not in data["draft_letter"]
    assert ">" not in data["draft_letter"]

    assert "extracted_key_points" in data
    assert "quoted_clauses" in data and isinstance(data["quoted_clauses"], list)
    assert "similar_letters" in data and isinstance(data["similar_letters"], list)
    assert "structure_summary" in data and isinstance(data["structure_summary"], str)

    # Cleanup overrides
    app.dependency_overrides.pop(dp.get_db, None)
    app.dependency_overrides.pop(dp.get_current_user, None)
