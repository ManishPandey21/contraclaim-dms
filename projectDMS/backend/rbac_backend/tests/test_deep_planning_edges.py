import asyncio
from typing import Any, Dict, List, Optional
from fastapi.testclient import TestClient

import rbac_backend.routers.deep_planning as dp
from rbac_backend.main import app
from rbac_backend.core.security import CurrentUser


# ----- Helpers -----
class _FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    async def to_list(self, length: Optional[int] = None):
        return self._rows[: (length or len(self._rows))]


class _FakeLetters:
    def __init__(self, by_id: Dict[Any, Dict[str, Any]]):
        self._by_id = by_id

    async def find_one(self, filt: Dict[str, Any]):
        # Expect {"_id": ObjectId("..")} or {"_id": "raw-id"}, or the scoped
        # target lookup {"$and": [{"_id": {"$in": [...]}}, <scope>]}. These
        # tests run as an unselected superadmin, whose scope is {}.
        if "$and" in filt:
            id_clause, scope = filt["$and"]
            assert scope == {}, scope
            for candidate in id_clause["_id"]["$in"]:
                if candidate in self._by_id:
                    return self._by_id[candidate]
            return None
        return self._by_id.get(filt.get("_id"))

    def find(self, _filter: Dict[str, Any]):
        return _FakeCursor([])

    async def update_one(self, *_args, **_kwargs):
        return {"acknowledged": True, "matched_count": 1, "modified_count": 1}


class _FakeDocs:
    def __init__(self, store: Dict[str, Dict[str, Any]]):
        self._store = store

    async def find_one(self, filt: Dict[str, Any]):
        _id = filt.get("_id")
        return self._store.get(_id)


class _FakeDB:
    def __init__(self, documents_store: Dict[str, Dict[str, Any]], letters_store: Dict[Any, Dict[str, Any]]):
        self.documents = _FakeDocs(documents_store)
        self.letters = _FakeLetters(letters_store)


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


def _override_auth_and_db(fake_db):
    async def fake_get_db():
        yield fake_db

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

    app.dependency_overrides[dp.get_db] = fake_get_db
    app.dependency_overrides[dp.get_current_user] = fake_current_user


def _clear_overrides():
    app.dependency_overrides.pop(dp.get_db, None)
    app.dependency_overrides.pop(dp.get_current_user, None)


# ----- Tests -----

def test_invalid_target_letter_id_graceful(monkeypatch):
    """
    Passing a malformed target_letter_id should be handled gracefully (no crash).
    """
    # Minimal fake DB; not used because ObjectId conversion will fail and be caught.
    fake_db = _FakeDB(documents_store={}, letters_store={})
    _override_auth_and_db(fake_db)

    # Patch embeddings to avoid real call
    monkeypatch.setattr(dp.client.embeddings, "create", lambda model, input: type("X", (), {"data": [type("Y", (), {"embedding": [0.1]})()]})())

    # Patch chat to return a simple draft
    def fake_chat_create(model: str, messages: List[Dict[str, str]], max_tokens: int, temperature: float):
        return _make_fake_chat_response("Date: 2025-01-01\n\nSubject: X\n\nDear Sir/Madam,\n\nBody...\n\nYours faithfully,\n")
    monkeypatch.setattr(dp.client.chat.completions, "create", fake_chat_create)

    client = TestClient(app)
    payload = {
        "document_ids": [],
        "subject": "Subject X",
        "recipient": "Owner",
        "user_id": "u",
        "target_letter_id": "not-an-objectid"  # malformed
    }
    resp = client.post("/api/deep-planning/generate-draft", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data.get("draft_letter"), str)

    _clear_overrides()


def test_target_letter_large_ocr_truncation(monkeypatch):
    """
    Ensure long ocrText is truncated and appears in prompt as '...(truncated)'.
    """
    long_text = "X" * 1700
    letters_store = {
        # Endpoint tries str id lookup when ObjectId conversion fails for raw id.
        "tl-1": {
            "summary": "A brief summary.",
            "keywords": ["delay", "inclement weather"],
            "contractual_clauses": ["Clause 8.4", "Clause 12.1"],
            "references": ["Client Letter 2023-10-20"],
            "ocrText": long_text,
        }
    }
    fake_db = _FakeDB(documents_store={}, letters_store=letters_store)
    _override_auth_and_db(fake_db)

    captured = {"user_msg": ""}

    # Avoid embeddings DB scan
    monkeypatch.setattr(dp.client.embeddings, "create", lambda model, input: type("X", (), {"data": [type("Y", (), {"embedding": [0.1, 0.1]})()]})())

    def fake_chat_create(model: str, messages: List[Dict[str, str]], max_tokens: int, temperature: float):
        # capture user content to inspect prompt
        for m in messages:
            if m["role"] == "user":
                captured["user_msg"] = m["content"]
        return _make_fake_chat_response("Date: 2025-01-01\n\nSubject: X\n\nDear Sir/Madam,\n\nBody...\n\nYours faithfully,\n")

    monkeypatch.setattr(dp.client.chat.completions, "create", fake_chat_create)

    client = TestClient(app)
    payload = {
        "document_ids": [],
        "subject": "Subject X",
        "recipient": "Owner",
        "user_id": "u",
        "target_letter_id": "tl-1"
    }
    resp = client.post("/api/deep-planning/generate-draft", json=payload)
    assert resp.status_code == 200, resp.text
    # Verify prompt contained truncated OCR block
    um = captured.get("user_msg", "")
    assert "OCR Text (excerpts):" in um
    assert "(truncated)" in um

    _clear_overrides()


def test_similar_letters_mixed_types_do_not_break(monkeypatch):
    """
    generate_draft_with_ai should tolerate non-dict entries in similar_letters.
    """
    calls = {"ok": False}

    # patch chat
    def fake_chat_create(model: str, messages: List[Dict[str, str]], max_tokens: int, temperature: float):
        calls["ok"] = True
        return _make_fake_chat_response("Date: 2025-01-01\n\nSubject: X\n\nDear Sir/Madam,\n\nBody...\n\nYours faithfully,\n")

    monkeypatch.setattr(dp.client.chat.completions, "create", fake_chat_create)

    # Call function directly
    asyncio.run(dp.generate_draft_with_ai(
        subject="S",
        recipient="R",
        document_context="Doc meta",
        user_context=None,
        points=None,
        similar_letters=[{"subject": "Valid"}, "bad", 123, None],  # mixed types
        target_letter_info=None
    ))
    assert calls["ok"] is True


def test_openai_failure_returns_500(monkeypatch):
    """
    When OpenAI fails, endpoint should return 500 with message.
    """
    fake_db = _FakeDB(documents_store={}, letters_store={})
    _override_auth_and_db(fake_db)

    # Patch embeddings minimal
    monkeypatch.setattr(dp.client.embeddings, "create", lambda model, input: type("X", (), {"data": [type("Y", (), {"embedding": [0.2]})()]})())

    def raise_chat(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(dp.client.chat.completions, "create", raise_chat)

    client = TestClient(app)
    payload = {
        "document_ids": [],
        "subject": "Subject X",
        "recipient": "Owner",
        "user_id": "u"
    }
    resp = client.post("/api/deep-planning/generate-draft", json=payload)
    assert resp.status_code == 500
    j = resp.json()
    assert "AI draft generation failed" in (j.get("detail") or "")

    _clear_overrides()


def test_embedding_failure_does_not_block(monkeypatch):
    """
    If embeddings fail in find_similar_letters, endpoint should continue and return 200.
    """
    fake_db = _FakeDB(documents_store={}, letters_store={})
    _override_auth_and_db(fake_db)

    def raise_embed(*args, **kwargs):
        raise RuntimeError("emb-fail")
    monkeypatch.setattr(dp.client.embeddings, "create", raise_embed)

    # Chat returns a simple draft
    monkeypatch.setattr(dp.client.chat.completions, "create",
                        lambda **kwargs: _make_fake_chat_response("Date: 2025-01-01\n\nSubject: X\n\nDear Sir/Madam,\n\nBody...\n\nYours faithfully,\n"))

    client = TestClient(app)
    payload = {
        "document_ids": [],
        "subject": "Subject X",
        "recipient": "Owner",
        "user_id": "u"
    }
    resp = client.post("/api/deep-planning/generate-draft", json=payload)
    assert resp.status_code == 200, resp.text

    _clear_overrides()
