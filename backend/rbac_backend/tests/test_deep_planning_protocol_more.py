import asyncio
from typing import Any, Dict, List
from bson import ObjectId


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


def test_generate_draft_with_ai_missing_info_defaults(monkeypatch):
    """
    When user_context/points are missing and no similar letters provided:
    - Prompt should include '- None provided' sections for facts/points.
    - Similar letters section should render 'None provided'.
    """
    import rbac_backend.routers.deep_planning as dp

    call_args: Dict[str, Any] = {}

    def fake_create(model: str, messages: List[Dict[str, str]], max_tokens: int, temperature: float):
        call_args["model"] = model
        call_args["messages"] = messages
        call_args["max_tokens"] = max_tokens
        call_args["temperature"] = temperature
        return _make_fake_chat_response("Letter with minimal data")

    monkeypatch.setattr(dp.client.chat.completions, "create", fake_create)

    # Inputs with missing info
    subject = "Payment Reconciliation"
    recipient = ""
    document_context = ""  # no docs
    user_context = None
    points = None
    similar_letters = None

    _ = asyncio.run(dp.generate_draft_with_ai(
        subject=subject,
        recipient=recipient,
        document_context=document_context,
        user_context=user_context,
        points=points,
        similar_letters=similar_letters
    ))

    msgs = call_args.get("messages") or []
    usr = msgs[1]["content"]
    # Check defaults
    assert "- None provided" in usr  # at least once (facts or points)
    assert "Similar letters for style only" in usr
    assert "None provided" in usr  # for similar letters
    assert "Plain text" in usr or "plain text" in usr


def test_extract_document_content_formats_list(monkeypatch):
    """
    Validates extract_document_content builds a readable list of document metadata.
    """
    import rbac_backend.routers.deep_planning as dp

    class FakeDocs:
        def __init__(self, store):
            self.store = store

        async def find_one(self, filt):
            _id = filt.get("_id")
            return self.store.get(_id)

    class FakeDB:
        def __init__(self, store):
            self.documents = FakeDocs(store)

    # Build fake documents keyed by ObjectId
    oid1 = ObjectId()
    oid2 = ObjectId()
    store = {
        oid1: {
            "filename": "GCC_SCC.pdf",
            "subject": "General & Special Conditions",
            "date": "2023-11-01",
            "from_": "Owner",
            "to": "Contractor",
            "references": ["Clause 8.4", "Clause 12.1"]
        },
        oid2: {
            "filename": "Daily_Report_284.txt",
            "subject": "Daily Site Report #284",
            "date": "2023-10-26",
            "from_": "Site Engineer",
            "to": "Document Control",
            "references": []
        }
    }

    db = FakeDB(store)
    # Pass as strings (function converts to ObjectId)
    doc_ids = [str(oid1), str(oid2)]
    text = asyncio.run(dp.extract_document_content(db, doc_ids))

    # Check expected fields are present
    assert "Document: GCC_SCC.pdf" in text
    assert "Subject: General & Special Conditions" in text
    assert "References:" in text
    assert "  - Clause 8.4" in text
    assert "Document: Daily_Report_284.txt" in text
    assert "Daily Site Report #284" in text
