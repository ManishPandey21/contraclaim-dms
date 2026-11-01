"""G-A6 / R16: drafting v2 may only ground a draft in AUTHORISED prior correspondence.

`DraftContextBuilder._prior_correspondence_sources` is the letter-drafting v2
engine's copy of the defect closed for the LangGraph pipeline in G-A5. It calls
``ConversationService.get_conversation_chain(letter_id, current_user=...)`` — the
method that accepts the actor and never reads it — and then filters the family it
gets back against the **LETTER's** ``organization_id`` / ``project_id``.

That is a workspace-consistency check wearing an authority costume. Two things
follow from it:

* ``Letter.project_id`` is OPTIONAL. For an organisation-level anchor the
  project comparison is skipped entirely (``if project_id and ...``), so every
  project in the tenant is waved through into the drafting context;
* even when it does compare, it compares against the anchor letter rather than
  the caller's entitlement, so the boundary is "what workspace is this letter
  in?" and never "what may this actor see?".

The conversation family itself is assembled from association alone — a shared
``conversation_id`` or a ``previous_letter_id`` edge. Association is not
authority: a reply chain may cross projects and organisations. **Authorisation
to the parent letter does not transfer to its thread.**

What makes this material rather than transient is where the result comes to
rest. ``DraftRunService.create_run`` persists the builder's output into a
``DraftRun`` (``context_bundle.prior_correspondence_ids`` and the ``sources``
ledger), into an immutable ``letter_draft_evidence_snapshots`` row, and into a
gzipped ``draft_context_packs`` document whose ``prior_correspondence`` list
carries each snippet. Unauthorised material therefore becomes durable state that
every later reader inherits without revisiting the original authority.

Every test here asserts on four surfaces, not one:

1. the rendered LLM prompt, captured before any answer exists — proving the
   filtering happens BEFORE prompt construction and BEFORE model invocation.
   Stripping a marker out of the answer afterwards is too late, because the
   model already saw it;
2. the returned ``DraftRun``;
3. the ``letter_draft_runs`` row re-read from storage;
4. the immutable evidence snapshot and the decompressed context pack.

Provenance is asserted separately from text: a foreign letter id may not survive
in ``prior_correspondence_ids``, in a source's ``letter_id``, in the evidence
snapshot or in the context pack merely because its summary was removed.

The positives are load-bearing. In-scope prior correspondence must still reach
the draft, and a global actor must keep the breadth canonical authorisation
gives them. A containment that empties prior correspondence is an outage, not a
boundary.

RED 10 (raw graph poisoning) is **NOT APPLICABLE** to this seam and is not
manufactured here: ``_prior_correspondence_sources`` reads ``db.letters`` only,
through ``ConversationService``. The builder's separate ``_graph_sources`` does
read Falkor and is already pinned by ``test_graph_consumer_authority.py``.
"""

from __future__ import annotations

import gzip
import json
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest
from bson import ObjectId

from rbac_backend.models.letter_drafting import DraftRun, DraftRunCreateRequest
from rbac_backend.services import conversation_service as conversation_service_module
from rbac_backend.services.letter_drafting import context as context_module
from rbac_backend.services.letter_drafting import generator as generator_module
from rbac_backend.services.letter_drafting.context import DraftContextBuilder
from rbac_backend.services.letter_drafting.service import DraftRunService

# --- unique markers -------------------------------------------------------
#
# Every marker is unique so an assertion can never pass because the string
# happened to be absent for an unrelated reason.

SIBLING_PROJECT_MARKER = "PROJECT_B_V2_PRIOR_CONFIDENTIAL_20260831"
FOREIGN_ORG_MARKER = "ORG_B_V2_PRIOR_CONFIDENTIAL_20260831"
SAME_PROJECT_MARKER = "PROJECT_A_V2_PRIOR_AUTHORISED_20260831"
CONVERSATION_ONLY_MARKER = "CONVERSATION_EDGE_V2_CONFIDENTIAL_20260831"


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo, faithful to the operators the production code uses
# ---------------------------------------------------------------------------


def _same(left: Any, right: Any) -> bool:
    return str(getattr(left, "value", left)) == str(getattr(right, "value", right))


def _field(document: Dict[str, Any], key: str) -> Tuple[bool, Any]:
    value: Any = document
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _matches(document: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_matches(document, branch) for branch in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
            continue
        exists, actual = _field(document, key)
        if isinstance(expected, dict):
            if "$exists" in expected and exists is not bool(expected["$exists"]):
                return False
            if "$in" in expected and not any(_same(actual, c) for c in expected["$in"]):
                return False
            if "$nin" in expected and any(_same(actual, c) for c in expected["$nin"]):
                return False
            if "$ne" in expected and _same(actual, expected["$ne"]):
                return False
            continue
        if not _same(actual, expected):
            return False
    return True


def _sort_key(row: Dict[str, Any], field: str) -> Tuple[int, str]:
    value = row.get(field)
    if value is None:
        return (0, "")
    return (1, value.isoformat() if isinstance(value, datetime) else str(value))


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, key: Any = None, direction: int = 1) -> "_Cursor":
        if isinstance(key, str):
            self.rows.sort(key=lambda row: _sort_key(row, key), reverse=direction < 0)
        return self

    def skip(self, amount: int) -> "_Cursor":
        self.rows = self.rows[amount:]
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return deepcopy(self.rows if length is None else self.rows[:length])

    def __aiter__(self):
        async def _generate():
            for row in self.rows:
                yield deepcopy(row)

        return _generate()


class _Collection:
    def __init__(self, rows: Iterable[Dict[str, Any]] = ()) -> None:
        self.rows = [deepcopy(row) for row in rows]

    async def find_one(self, query=None, projection=None, sort=None):
        matches = [row for row in self.rows if _matches(row, query)]
        if sort:
            field, direction = sort[0]
            matches.sort(key=lambda row: _sort_key(row, field), reverse=direction < 0)
        return deepcopy(matches[0]) if matches else None

    def find(self, query=None, *_a: Any, **_k: Any) -> _Cursor:
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def count_documents(self, query=None, **_k: Any) -> int:
        return len([row for row in self.rows if _matches(row, query)])

    async def insert_one(self, document: Dict[str, Any]):
        inserted = deepcopy(document)
        inserted.setdefault("_id", ObjectId())
        self.rows.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    async def update_one(self, query, update, upsert: bool = False, **_k: Any):
        for row in self.rows:
            if not _matches(row, query):
                continue
            row.update(deepcopy(update.get("$set", {})))
            for key, amount in (update.get("$inc") or {}).items():
                row[key] = int(row.get(key) or 0) + int(amount)
            for key, value in (update.get("$push") or {}).items():
                row.setdefault(key, []).append(deepcopy(value))
            return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            stored = {k: v for k, v in (query or {}).items() if not k.startswith("$")}
            stored.update(deepcopy(update.get("$set", {})))
            stored.setdefault("_id", ObjectId())
            self.rows.append(stored)
            return SimpleNamespace(matched_count=0, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def create_index(self, *_a: Any, **_k: Any) -> str:  # pragma: no cover
        return "index"


class _Database:
    def __init__(self, letters: List[Dict[str, Any]]) -> None:
        self._collections: Dict[str, _Collection] = {"letters": _Collection(letters)}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


# ---------------------------------------------------------------------------
# Stubs — everything outside this boundary is held still on purpose
# ---------------------------------------------------------------------------


class _DisabledFalkor:
    """The graph is a different seam (pinned by test_graph_consumer_authority)."""

    enabled = False

    def get_thread(self, *_a: Any, **_k: Any) -> List[Dict[str, Any]]:  # pragma: no cover
        return []


class _NoContracts:
    """Clause retrieval is not part of this boundary; keep Qdrant out of the run."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def search_contracts(self, *_a: Any, **_k: Any):
        return SimpleNamespace(results=[])


class _RecordingLLM:
    """Captures every rendered prompt BEFORE an answer exists."""

    prompts: List[str] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def generate(self, prompt: str, **_k: Any) -> str:
        type(self).prompts.append(prompt)
        return (
            "Draft Letter:\nDeterministic source-grounded draft.\n\n"
            "Source Integrity Notes:\nGrounded in the authorised ledger only."
        )


NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def _letter(
    letter_id: Any,
    *,
    organization_id: str = "org-A",
    project_id: Optional[str] = "proj-A",
    subject: str = "Anchor letter",
    content: str = "Prepare a source-grounded response.",
    **overrides: Any,
) -> Dict[str, Any]:
    letter = {
        "_id": letter_id,
        "title": subject,
        "recipient": "Engineer",
        "subject": subject,
        "content": content,
        "status": "Draft",
        "created_by": "user-A",
        "assigned_to": "user-A",
        "organization_id": organization_id,
        "project_id": project_id,
        "letter_no": f"GA6-{str(letter_id)[-6:]}",
        "date": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    letter.update(overrides)
    return letter


def _project_actor() -> SimpleNamespace:
    """Organisation A, project A only. The narrowest realistic drafting actor."""
    return SimpleNamespace(
        id="user-A",
        roles=["projectuser"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-A"],
    )


def _two_project_actor() -> SimpleNamespace:
    """Organisation A, projects A AND B — entitled to the sibling project."""
    return SimpleNamespace(
        id="user-AB",
        roles=["projectuser"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-A", "proj-B"],
    )


def _global_actor() -> SimpleNamespace:
    """A global actor, resolved through canonical authorization — not by name."""
    return SimpleNamespace(
        id="user-super",
        roles=["superadmin"],
        organization_id="org-A",
        organizations=["org-A", "org-B"],
        projects=["proj-A", "proj-B"],
    )


class _Run:
    """Every place drafting material can come to rest on the v2 path."""

    def __init__(self, run: DraftRun, db: _Database, prompts: List[str]) -> None:
        self.run = run
        self.db = db
        self.prompts = prompts

    @property
    def stored_row(self) -> Dict[str, Any]:
        for row in self.db["letter_draft_runs"].rows:
            if row.get("run_id") == self.run.run_id:
                return row
        return {}

    @property
    def evidence_snapshot(self) -> Dict[str, Any]:
        for row in self.db["letter_draft_evidence_snapshots"].rows:
            if row.get("run_id") == self.run.run_id:
                return row
        return {}

    @property
    def context_pack(self) -> Dict[str, Any]:
        for row in self.db["draft_context_packs"].rows:
            if row.get("run_id") != self.run.run_id:
                continue
            blob = row.get("compressed_data")
            if blob is None:
                return dict(row)
            return json.loads(gzip.decompress(bytes(blob)).decode("utf-8"))
        return {}

    def surfaces(self) -> Dict[str, str]:
        return {
            "the rendered LLM prompt (filtering must precede synthesis)": "\n".join(
                self.prompts
            ),
            "the returned DraftRun": repr(self.run.model_dump(mode="json")),
            "the PERSISTED letter_draft_runs row": repr(self.stored_row),
            "the IMMUTABLE evidence snapshot": repr(self.evidence_snapshot),
            "the PERSISTED context pack": repr(self.context_pack),
        }

    def provenance(self) -> str:
        return repr(
            {
                "prior_correspondence_ids": self.run.context_bundle.prior_correspondence_ids,
                "source_letter_ids": [source.letter_id for source in self.run.sources],
                "source_ids": [source.source_id for source in self.run.sources],
                "stored_context_bundle": self.stored_row.get("context_bundle"),
                "stored_sources": self.stored_row.get("sources"),
                "evidence_snapshot_sources": self.evidence_snapshot.get("sources"),
                "context_pack_prior": self.context_pack.get("prior_correspondence"),
                "context_pack_source_ids": self.context_pack.get("source_ids"),
            }
        )


def _request(**overrides: Any) -> DraftRunCreateRequest:
    payload: Dict[str, Any] = {
        "mode": "draft",
        "draft_type": "fresh",
        "role": "contractor",
        "subject": "Notice of delay",
        "recipient": "Engineer",
        "purpose": "Record the delay and reserve entitlement.",
        "requirements": "State the contractual basis and the action required.",
        "trigger_event": "Access to the north zone was withheld.",
        "required_action": "Confirm revised access within seven days.",
        "plan_override": "1. Facts. 2. Contractual basis. 3. Required action.",
    }
    payload.update(overrides)
    return DraftRunCreateRequest(**payload)


async def _create_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    letters: List[Dict[str, Any]],
    letter_id: ObjectId,
    actor: Any = None,
    runs: int = 1,
    request: Optional[DraftRunCreateRequest] = None,
) -> _Run:
    """Drive the REAL v2 drafting service and its REAL DraftRun persistence.

    Only the parent-letter gate is stubbed, and it is stubbed to GRANT: that is
    precisely the premise under test — the actor IS authorised for the anchor
    letter. Nothing about the prior-correspondence boundary is bypassed.
    """
    db = _Database(letters)
    _RecordingLLM.prompts = []

    async def _get_db():
        return db

    monkeypatch.setattr(conversation_service_module, "get_database", _get_db)
    monkeypatch.setattr(context_module, "ContractService", _NoContracts)
    monkeypatch.setattr(context_module, "FalkorGraphService", lambda *a, **k: _DisabledFalkor())
    monkeypatch.setattr(generator_module, "LLMGenerator", _RecordingLLM)

    service = DraftRunService(db)

    async def _parent_letter_is_authorised(target_id: str, *_a: Any, **_k: Any):
        return await service.letter_service.get_letter(str(target_id))

    monkeypatch.setattr(service, "_load_and_authorize", _parent_letter_is_authorised)

    run: Optional[DraftRun] = None
    for _ in range(runs):
        run = await service.create_run(
            str(letter_id), request or _request(), actor or _project_actor()
        )
    assert run is not None
    return _Run(run, db, list(_RecordingLLM.prompts))


def _assert_absent(run: _Run, marker: str, why: str) -> None:
    for surface, text in run.surfaces().items():
        assert marker not in text, (
            f"{why}: the marker {marker!r} reached {surface}. Association is not "
            "authority — a shared conversation_id or a previous_letter_id edge "
            "does not entitle this actor to the linked letter's content."
        )


def _assert_present(run: _Run, marker: str, why: str) -> None:
    combined = "\n".join(run.surfaces().values())
    assert marker in combined, (
        f"{why}: the authorised marker {marker!r} reached NONE of the drafting "
        "surfaces. Containment that empties prior correspondence is an outage, "
        "not a boundary."
    )


def _assert_id_absent(run: _Run, letter_id: ObjectId, why: str) -> None:
    assert str(letter_id) not in run.provenance(), (
        f"{why}: the unauthorised letter id {letter_id} survived in the persisted "
        "DraftRun provenance. Removing the text while keeping the id still "
        "records an unauthorised source as a contributor to this draft, and the "
        "evidence snapshot is immutable."
    )


# ---------------------------------------------------------------------------
# RED 1 — sibling project inside the same organisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_project_prior_letter_is_not_drafted_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The anchor is ORGANISATION-level, so the letter-workspace comparison
    that used to stand in for authority had no project value to compare and
    waved every project in the tenant into the draft."""
    # The anchor points at the sibling-project letter, which is exactly the
    # association a reply chain creates.
    anchor_id, prior_id = ObjectId(), ObjectId()
    run = await _create_run(
        monkeypatch,
        letters=[
            _letter(anchor_id, project_id=None, previous_letter_id=str(prior_id)),
            _letter(
                prior_id,
                project_id="proj-B",
                subject="Sibling project position",
                content=SIBLING_PROJECT_MARKER,
                summary=SIBLING_PROJECT_MARKER,
            ),
        ],
        letter_id=anchor_id,
    )

    _assert_absent(run, SIBLING_PROJECT_MARKER, "sibling-project prior correspondence")
    _assert_id_absent(run, prior_id, "sibling-project prior correspondence")


# ---------------------------------------------------------------------------
# RED 2 — foreign organisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_foreign_organisation_prior_letter_is_not_drafted_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_id, prior_id = ObjectId(), ObjectId()
    run = await _create_run(
        monkeypatch,
        letters=[
            _letter(anchor_id, project_id=None, previous_letter_id=str(prior_id)),
            _letter(
                prior_id,
                organization_id="org-B",
                project_id="proj-B",
                subject="Another tenant's position",
                content=FOREIGN_ORG_MARKER,
                summary=FOREIGN_ORG_MARKER,
            ),
        ],
        letter_id=anchor_id,
    )

    _assert_absent(run, FOREIGN_ORG_MARKER, "cross-tenant prior correspondence")
    _assert_id_absent(run, prior_id, "cross-tenant prior correspondence")


# ---------------------------------------------------------------------------
# RED 3 — authorisation to the parent letter does not transfer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorisation_to_the_parent_letter_does_not_admit_its_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ONLY link here is a shared `conversation_id`.

    The actor is authorised for the anchor — `_load_and_authorize` is stubbed to
    grant it — and that is the whole point: entitlement to one letter may never
    be inherited by everything associated with it. This must stay true however
    the family traversal is later refactored.

    These two rows carry STRING `_id` values on purpose. `_load_conversation_family`
    hydrates the `conversation_id` branch with a bare `Letter(**doc)` inside a
    swallowing `except`, and `Letter.id` is typed `Optional[str]`, so an
    ObjectId-backed row is silently dropped there (the correctness defect
    recorded in G-A4; it is NOT fixed here). Writing this case with ObjectIds
    would make it pass for that reason instead of for the authority boundary —
    a vacuous green. String ids exercise the branch as it will behave once that
    defect is fixed, which is also the answer to "does R16 containment survive
    the widening?": it must, and this pins it.
    """
    anchor_id, prior_id = str(ObjectId()), str(ObjectId())
    conversation = str(ObjectId())
    run = await _create_run(
        monkeypatch,
        letters=[
            _letter(anchor_id, project_id=None, conversation_id=conversation),
            _letter(
                prior_id,
                project_id="proj-B",
                conversation_id=conversation,
                subject="Same conversation, different project",
                content=CONVERSATION_ONLY_MARKER,
                summary=CONVERSATION_ONLY_MARKER,
            ),
        ],
        letter_id=anchor_id,
    )

    # Non-vacuity: the conversation branch must actually have reached the prior
    # letter, otherwise this test would pass because the traversal found
    # nothing rather than because the boundary held.
    from rbac_backend.services.conversation_service import ConversationService
    from rbac_backend.services.letter_service import LetterService

    family = await ConversationService(
        LetterService(run.db)
    )._load_conversation_family(
        await LetterService(run.db).get_letter(anchor_id)  # type: ignore[arg-type]
    )
    assert prior_id in family, (
        "the conversation-association traversal did not reach the prior letter, "
        "so this test proves nothing about the authority boundary"
    )

    _assert_absent(
        run, CONVERSATION_ONLY_MARKER, "conversation-association prior correspondence"
    )
    _assert_id_absent(run, prior_id, "conversation-association prior correspondence")


# ---------------------------------------------------------------------------
# RED 4 — the actor is load-bearing, not decorative
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_actor_decides_prior_correspondence_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same world, same anchor, two different actors, two different answers.

    Asserting that the helper *accepts* `current_user` proves nothing — the old
    code accepted it too. This proves it is READ.
    """
    anchor_id, prior_id = ObjectId(), ObjectId()
    world = [
        _letter(anchor_id, project_id=None, previous_letter_id=str(prior_id)),
        _letter(
            prior_id,
            project_id="proj-B",
            subject="Sibling project position",
            content=SIBLING_PROJECT_MARKER,
            summary=SIBLING_PROJECT_MARKER,
        ),
    ]

    narrow = await _create_run(
        monkeypatch, letters=world, letter_id=anchor_id, actor=_project_actor()
    )
    _assert_absent(narrow, SIBLING_PROJECT_MARKER, "actor entitled to project A only")

    wide = await _create_run(
        monkeypatch, letters=world, letter_id=anchor_id, actor=_two_project_actor()
    )
    _assert_present(
        wide,
        SIBLING_PROJECT_MARKER,
        "actor entitled to BOTH projects",
    )


# ---------------------------------------------------------------------------
# RED 5 / RED 6 — the persisted DraftRun, text and provenance separately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_persisted_draftrun_records_only_authorised_prior_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DraftRun is durable state, not a response the caller may discard.

    Three persisted artefacts are inspected by re-reading storage rather than
    by trusting the returned object: the `letter_draft_runs` row, the immutable
    `letter_draft_evidence_snapshots` row (which keeps `letter_id` per source
    even though it drops the text), and the gzipped context pack.
    """
    anchor_id, authorised_id, foreign_id = ObjectId(), ObjectId(), ObjectId()
    run = await _create_run(
        monkeypatch,
        letters=[
            _letter(anchor_id, project_id=None, previous_letter_id=str(authorised_id)),
            _letter(
                authorised_id,
                project_id="proj-A",
                subject="In-scope position",
                content=SAME_PROJECT_MARKER,
                summary=SAME_PROJECT_MARKER,
                previous_letter_id=str(foreign_id),
            ),
            _letter(
                foreign_id,
                organization_id="org-B",
                project_id="proj-B",
                subject="Another tenant's position",
                content=FOREIGN_ORG_MARKER,
                summary=FOREIGN_ORG_MARKER,
            ),
        ],
        letter_id=anchor_id,
    )

    stored = run.stored_row
    assert stored, "the DraftRun was not persisted at all"
    bundle = stored.get("context_bundle") or {}
    assert str(foreign_id) not in (bundle.get("prior_correspondence_ids") or []), (
        "the foreign-organisation letter id is recorded as a prior-correspondence "
        "contributor in the persisted DraftRun context bundle"
    )
    snapshot_letters = {
        str(row.get("letter_id") or "")
        for row in (run.evidence_snapshot.get("sources") or [])
    }
    assert str(foreign_id) not in snapshot_letters, (
        "the foreign-organisation letter id survived in the IMMUTABLE evidence "
        "snapshot. That row is never rewritten, so an unauthorised source would "
        "be recorded as a contributor permanently."
    )
    _assert_absent(run, FOREIGN_ORG_MARKER, "two-hop cross-tenant thread")
    _assert_id_absent(run, foreign_id, "two-hop cross-tenant thread")

    # ...and the lawful hop in the SAME chain still contributes, so the
    # containment is a filter and not a switch.
    _assert_present(run, SAME_PROJECT_MARKER, "in-scope hop of a mixed thread")


# ---------------------------------------------------------------------------
# RED 7 — the positive case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_in_scope_prior_letter_still_grounds_the_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_id, prior_id = ObjectId(), ObjectId()
    run = await _create_run(
        monkeypatch,
        letters=[
            _letter(anchor_id, previous_letter_id=str(prior_id)),
            _letter(
                prior_id,
                subject="Our position of 12 August",
                content=SAME_PROJECT_MARKER,
                summary=SAME_PROJECT_MARKER,
            ),
        ],
        letter_id=anchor_id,
    )

    _assert_present(run, SAME_PROJECT_MARKER, "same-project prior correspondence")
    assert str(prior_id) in run.provenance(), (
        "an authorised prior letter must be RECORDED as a contributor; dropping "
        "its provenance would make the draft unauditable"
    )


# ---------------------------------------------------------------------------
# RED 8 — global actor semantics, resolved canonically
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_global_actor_keeps_the_breadth_canonical_authorisation_gives_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No role-name check decides this — `build_scope_query` does.

    The anchor is organisation-level, so a global actor's canonical scope for
    this thread is organisation A. The sibling project inside org A is therefore
    admitted; the other tenant's letter is not, because the anchor narrows
    INSIDE the entitlement.
    """
    anchor_id, sibling_id, foreign_id = ObjectId(), ObjectId(), ObjectId()
    run = await _create_run(
        monkeypatch,
        letters=[
            _letter(anchor_id, project_id=None, previous_letter_id=str(sibling_id)),
            _letter(
                sibling_id,
                project_id="proj-B",
                subject="Sibling project position",
                content=SIBLING_PROJECT_MARKER,
                summary=SIBLING_PROJECT_MARKER,
                previous_letter_id=str(foreign_id),
            ),
            _letter(
                foreign_id,
                organization_id="org-B",
                project_id="proj-B",
                subject="Another tenant's position",
                content=FOREIGN_ORG_MARKER,
                summary=FOREIGN_ORG_MARKER,
            ),
        ],
        letter_id=anchor_id,
        actor=_global_actor(),
    )

    _assert_present(run, SIBLING_PROJECT_MARKER, "global actor inside organisation A")
    _assert_absent(run, FOREIGN_ORG_MARKER, "global actor anchored in organisation A")


# ---------------------------------------------------------------------------
# RED 9 — an unresolvable prior identity fails closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unresolvable_previous_letter_id_contributes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The thread is hydrated canonically from `db.letters`, so a dangling edge
    has nothing to fall back on. Pinned rather than declared."""
    anchor_id = ObjectId()
    missing_id = ObjectId()
    run = await _create_run(
        monkeypatch,
        letters=[_letter(anchor_id, previous_letter_id=str(missing_id))],
        letter_id=anchor_id,
    )

    assert run.run.context_bundle.prior_correspondence_ids == [], (
        "a `previous_letter_id` that resolves to no canonical letter produced a "
        "prior-correspondence entry anyway"
    )
    _assert_id_absent(run, missing_id, "dangling previous_letter_id")


# ---------------------------------------------------------------------------
# RED 11 — a fresh run must not inherit a contaminated predecessor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_run_rebuilds_its_context_and_inherits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`create_run` is the only way a v2 DraftRun is born, and it rebuilds the
    context bundle from source every time — retries and revisions included. The
    only state carried between runs is `_add_governance_comment_context`, which
    copies reviewer comments, never source material. Two consecutive runs must
    therefore both be clean."""
    anchor_id, prior_id = ObjectId(), ObjectId()
    run = await _create_run(
        monkeypatch,
        letters=[
            _letter(anchor_id, project_id=None, previous_letter_id=str(prior_id)),
            _letter(
                prior_id,
                project_id="proj-B",
                subject="Sibling project position",
                content=SIBLING_PROJECT_MARKER,
                summary=SIBLING_PROJECT_MARKER,
            ),
        ],
        letter_id=anchor_id,
        runs=2,
    )

    assert len(run.db["letter_draft_runs"].rows) == 2, (
        "the second call did not create its own run, so this asserts nothing"
    )
    for row in run.db["letter_draft_runs"].rows:
        assert SIBLING_PROJECT_MARKER not in repr(row), (
            "an unauthorised marker reached a persisted DraftRun on a repeat run"
        )
    _assert_absent(run, SIBLING_PROJECT_MARKER, "second consecutive run")


# ---------------------------------------------------------------------------
# The v3 background worker principal — fail closed, do not fabricate authority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rootless_worker_principal_admits_no_prior_correspondence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`LangGraphDraftingEngine.collect_evidence` builds this exact principal.

    It is synthesised from the LETTER — `SimpleNamespace(id=run.created_by,
    organization_id=letter.organization_id, project_id=letter.project_id)` — and
    carries no roles and no assignments. Authority derived from the resource
    being read is not authority, so canonical scope resolution denies it
    everything. That is the correct direction (fail closed) and this test pins
    it so a future "make v3 work again" change cannot quietly re-fabricate an
    entitlement instead of passing the real actor.
    """
    anchor_id, prior_id = ObjectId(), ObjectId()
    db = _Database(
        [
            _letter(anchor_id, previous_letter_id=str(prior_id)),
            _letter(
                prior_id,
                subject="Our position of 12 August",
                content=SAME_PROJECT_MARKER,
                summary=SAME_PROJECT_MARKER,
            ),
        ]
    )

    async def _get_db():
        return db

    monkeypatch.setattr(conversation_service_module, "get_database", _get_db)
    monkeypatch.setattr(context_module, "ContractService", _NoContracts)
    monkeypatch.setattr(context_module, "FalkorGraphService", lambda *a, **k: _DisabledFalkor())

    from rbac_backend.services.conversation_service import ConversationService
    from rbac_backend.services.document_service import DocumentService
    from rbac_backend.services.letter_service import LetterService

    letter_service = LetterService(db)
    letter = await letter_service.get_letter(str(anchor_id))
    builder = DraftContextBuilder(
        document_service=DocumentService(db),
        conversation_service=ConversationService(letter_service),
        db=db,
    )
    worker_principal = SimpleNamespace(
        id="user-A",
        organization_id=getattr(letter, "organization_id", None),
        project_id=getattr(letter, "project_id", None),
    )
    bundle, sources, warnings = await builder.build(letter, _request(), worker_principal)

    assert bundle.prior_correspondence_ids == [], (
        "a principal fabricated from the letter under construction was granted "
        "prior correspondence. Reading authority off the resource being read is "
        "circular: it authorises whatever it is asked about."
    )
    assert SAME_PROJECT_MARKER not in repr([source.model_dump() for source in sources])
