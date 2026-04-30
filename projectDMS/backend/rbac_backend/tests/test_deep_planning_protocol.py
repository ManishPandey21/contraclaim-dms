import asyncio
from typing import Any, Dict, List


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


def test_generate_draft_with_ai_builds_expert_protocol_prompt(monkeypatch):
    """
    Verifies:
    - System role is Expert Contract Manager with strict constraints (no placeholders, plain text).
    - User prompt contains key structural constraints and no-hallucination guidance.
    - Temperature is set to 0.2 and model is 'gpt-4'.
    - Function returns stripped content text.
    """
    import rbac_backend.routers.deep_planning as dp

    call_args: Dict[str, Any] = {}

    def fake_create(model: str, messages: List[Dict[str, str]], max_tokens: int, temperature: float):
        # capture call parameters for assertions
        call_args["model"] = model
        call_args["messages"] = messages
        call_args["max_tokens"] = max_tokens
        call_args["temperature"] = temperature
        # return a fake letter content
        return _make_fake_chat_response("Sample Letter Content\n")

    # Monkeypatch the OpenAI client call
    monkeypatch.setattr(dp.client.chat.completions, "create", fake_create)

    # Inputs
    subject = "Notification of Delay due to Inclement Weather"
    recipient = "Mr. Smith"
    document_context = "Document: GCC_SCC.pdf\nSubject: Conditions\nDate: 2023-11-01\n"
    user_context = "Event occurred on 2023-10-26 impacting Work Package 4B."
    points = "Formally notify the client as per Clause 8.4.\nRequest a 14-day extension."
    similar_letters = [{"subject": "Delay Notification - Power Outage", "recipient": "Owner", "created_at": "2023-10-27"}]

    # Execute
    result = asyncio.run(dp.generate_draft_with_ai(
        subject=subject,
        recipient=recipient,
        document_context=document_context,
        user_context=user_context,
        points=points,
        similar_letters=similar_letters
    ))

    # Assertions on OpenAI invocation
    assert call_args.get("model") == "gpt-4"
    assert call_args.get("temperature") == 0.2
    msgs = call_args.get("messages") or []
    assert isinstance(msgs, list) and len(msgs) >= 2

    # System message checks
    sys_msg = msgs[0]
    assert sys_msg["role"] == "system"
    sys_content = sys_msg["content"]
    assert "Expert Contract Manager" in sys_content
    assert "Output plain text only" in sys_content or "plain text only" in sys_content
    assert "never invent information" in sys_content.lower() or "do not invent" in sys_content.lower()

    # User prompt checks
    usr_msg = msgs[1]
    assert usr_msg["role"] == "user"
    usr_content = usr_msg["content"]
    assert "Letter Requirements & Structure" in usr_content
    assert "Assumptions: Strictly prohibited" in usr_content or "Assumptions: Strictly prohibited".lower() in usr_content.lower()
    assert "Do not fabricate or assume" in usr_content
    assert "Plain text" in usr_content or "plain text" in usr_content
    assert "Your Reference" in usr_content and "Our Reference" in usr_content
    assert "Formal Closing" in usr_content
    assert "Signature Block" in usr_content

    # Function output is stripped content
    assert result == "Sample Letter Content"


def test_extract_key_points_and_clauses_parsing(monkeypatch):
    """
    Verifies parsing logic of extract_key_points_and_clauses using a mocked OpenAI response.
    """
    import rbac_backend.routers.deep_planning as dp

    fake_text = """KEY POINTS:
- Event occurred on 2023-10-26 impacting Work Package 4B.
- Relevant contract section addresses delays.
- Recipient is responsible for site access.

QUOTED CLAUSES:
- Clause 8.4.i: Delay due to inclement weather (Page 12, Lines 10-20)
- Clause 12.1: Notification requirements (Page 3)
- Clause 20.5: Remedies available to contractor
"""

    def fake_create(model: str, messages: List[Dict[str, str]], max_tokens: int, temperature: float):
        return _make_fake_chat_response(fake_text)

    monkeypatch.setattr(dp.client.chat.completions, "create", fake_create)

    key_points, clauses = asyncio.run(dp.extract_key_points_and_clauses("dummy content for analysis"))
    assert "Event occurred on 2023-10-26" in key_points
    assert isinstance(clauses, list) and len(clauses) == 3

    # Check clause parsing fields presence
    c1 = clauses[0]
    assert "clause_number" in c1 and "content" in c1
    # Page/Lines optional
    assert c1.get("page_number") is not None or c1.get("line_numbers") is not None
