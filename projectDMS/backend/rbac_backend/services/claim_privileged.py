"""Privileged claim material: simulations and the strategy note.

Two things live here, in one collection that holds nothing else:

- a **simulation** of how another party is likely to respond to the claim, and
- the **strategy note**: where the claim is strong, where it is weak, and what
  risk remains.

Both are a party's own internal analysis. The wall around them is structural:

- This module is the only code that names the collection. No exporter, no
  evidence bundle, no arbitration source ledger and no matrix seeding reads
  it; ``test_claim_privilege_wall.py`` fails if another module starts to.
- Nothing is stored on the claim, where exports would pick it up, and skill
  output for these is not written to the shared step store.
- Reading and writing need ``dms.claim.strategy.read``, checked by the router.
- Nothing here writes a contractual fact, a finding or a determination. The
  only collection this module writes to is its own.

Every record carries its label, so a response or a screen cannot show one
without it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..models.claim import ELEMENT_KEYS, ClaimBench, ClaimHead, HeadElement
from ..models.claim_findings import (
    INTERNAL_ANALYSIS_LABEL,
    INTERNAL_NOTE_LABEL,
    ClaimSimulation,
    SimulatedHead,
    StrategyNote,
)
from ..models.claim_skill_outputs import (
    CLAIM_DETERMINATION_SCHEMA_KEY,
    STRATEGY_NOTE_SCHEMA_KEY,
    ClaimDeterminationOutput,
    StrategyNoteOutput,
)
from ..utils.datetime import now_utc
from .claim_determinations import (
    CLAIM_DETERMINATION_PROMPT,
    _ELEMENT_LABEL,
    head_position,
    hold_to_record,
    record_proposal,
)
from .letter_drafting.prompts import UNTRUSTED_CONTENT_GUARD, register_default_template
from .skills.system_skills import CLAIMS_SKILL_KEY

logger = logging.getLogger(__name__)

PRIVILEGED = "claim_privileged"
PARTIES = {"contractor", "engineer", "employer"}
MAX_SIMULATIONS_LISTED = 20

STRATEGY_NOTE_PROMPT = "claims.v1.strategy_note"
STRATEGY_NOTE_TEMPLATE = (
    """TASK: apply the skill above to the claim below, for the {role}'s own internal use.

List where the claim is strong, where it is weak, and what risk remains. This
note is privileged and is never issued to the other party.

The position on the record, head by head. You must not contradict it:
{facts}

OUTPUT: return one JSON object and nothing else, with exactly these keys:
{output_keys}

Rules:
- Never state a number of days or an amount of money that is not in the position above.
- Never invent facts, dates, clause wording or references.
- An element the record holds as weak or missing is a weakness, not a strength.

"""
    + UNTRUSTED_CONTENT_GUARD
    + "\n"
)
register_default_template(STRATEGY_NOTE_PROMPT, STRATEGY_NOTE_TEMPLATE)


class SimulationRequest(BaseModel):
    simulated_party: str


class StrategyNoteRequest(BaseModel):
    version: int = Field(..., ge=0)
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    residual_risks: List[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=8000)


class SimulationsResponse(BaseModel):
    label: str = INTERNAL_ANALYSIS_LABEL
    privileged: bool = True
    claim_id: str
    prepared_for: Optional[str] = None
    # The parties whose response can be modelled: every party but the one this project acts for.
    can_simulate: List[str] = Field(default_factory=list)
    current_board_version: Optional[int] = None
    items: List[ClaimSimulation] = Field(default_factory=list)


class StrategyNoteResponse(BaseModel):
    label: str = INTERNAL_NOTE_LABEL
    privileged: bool = True
    claim_id: str
    note: Optional[StrategyNote] = None
    current_board_version: Optional[int] = None


def _state(head: ClaimHead, key: str) -> Optional[str]:
    element = head.elements.get(key) or HeadElement()
    return element.state or element.proposed_state


def record_simulation(head: ClaimHead) -> SimulatedHead:
    """What the board alone says the other side can be expected to press. No result is predicted."""
    proposal = record_proposal(head)
    challenges = [
        f"{_ELEMENT_LABEL[key]} is held as {(_state(head, key) or 'not assessed').replace('_', ' ')} on the board"
        for key in ELEMENT_KEYS
        if _state(head, key) in (None, "weak", "missing")
    ]
    return SimulatedHead(
        head_id=head.head_id,
        head_title=head.title,
        likely_result="insufficient_information",
        reasoning=(
            "No response is predicted. Without an approved claims skill this lists only what the board itself "
            "shows to be open to challenge."
        ),
        likely_challenges=challenges,
        information_likely_requested=proposal.information_required,
        produced_by="record_check",
    )


def record_strategy(bench: ClaimBench) -> StrategyNoteOutput:
    strengths: List[str] = []
    weaknesses: List[str] = []
    risks: List[str] = []
    for head in bench.heads:
        for key in ELEMENT_KEYS:
            element = head.elements.get(key) or HeadElement()
            label = f"{head.head_id} {_ELEMENT_LABEL[key].lower()}"
            if element.state == "supported":
                strengths.append(f"{label}: decided supported" + (f" ({element.note})" if element.note else ""))
            elif element.state in {"weak", "missing"}:
                weaknesses.append(
                    f"{label}: decided {element.state}" + (f" ({element.note or element.proposed_reason})" if (element.note or element.proposed_reason) else "")
                )
            elif element.state is None:
                weaknesses.append(f"{label}: not yet decided")
        for risk in head.residual_risks:
            risks.append(f"{head.head_id}: {risk}")
        for finding in head.delay.findings.values():
            if finding.treatment != "not_true_concurrency":
                risks.append(f"{head.head_id}: an overlap is treated as {finding.treatment.replace('_', ' ')}; the other side may argue for a different treatment")
    return StrategyNoteOutput(strengths=strengths[:40], weaknesses=weaknesses[:40], residual_risks=risks[:40])


class ClaimPrivileged:
    def __init__(self, db: Any, bench: Any) -> None:
        self.db = db
        self.bench = bench

    @property
    def _collection(self) -> Any:
        return self.db[PRIVILEGED]

    async def _skill(self, claim: Dict[str, Any]) -> tuple[Any, Optional[str]]:
        version, _binding, reason = await self.bench.registry.resolve(
            CLAIMS_SKILL_KEY, str(claim.get("organization_id") or "") or None, claim.get("project_id")
        )
        return version, (None if version is not None else f"Skill not used: {reason or 'No published version'}")

    # ---------------------------------------------------------- simulations
    async def simulations(self, claim: Dict[str, Any]) -> SimulationsResponse:
        perspective = await self.bench._perspective(claim)
        bench = self.bench._bench_of(claim)
        rows = await self._collection.find({"claim_id": str(claim["_id"]), "kind": "simulation"}).to_list(length=500)
        items = sorted((ClaimSimulation(**row) for row in rows), key=lambda item: item.created_at, reverse=True)
        return SimulationsResponse(
            claim_id=str(claim["_id"]),
            prepared_for=perspective,
            can_simulate=sorted(PARTIES - {perspective}) if perspective else [],
            current_board_version=bench.board_version if bench else None,
            items=items[:MAX_SIMULATIONS_LISTED],
        )

    async def simulate(self, claim: Dict[str, Any], request: SimulationRequest, user_id: Optional[str]) -> ClaimSimulation:
        """Model another party's likely response. Reads the board; writes only here."""
        bench = self.bench._require_bench(claim)
        perspective = await self.bench._perspective(claim)
        if perspective is None:
            raise HTTPException(
                status_code=409,
                detail="State which party this project acts for before simulating another party's response.",
            )
        if request.simulated_party not in PARTIES:
            raise HTTPException(status_code=422, detail="simulated_party must be contractor, engineer or employer")
        if request.simulated_party == perspective:
            raise HTTPException(
                status_code=409,
                detail=f"This project acts as {perspective.title()}. Its own position is worked on the board, not simulated.",
            )
        version, unavailable = await self._skill(claim)
        heads: List[SimulatedHead] = []
        degraded_reasons: List[str] = [unavailable] if unavailable else []
        for head in bench.heads:
            floor = record_simulation(head)
            if version is None:
                heads.append(floor)
                continue
            position = head_position(head)
            result = await self.bench.runner.executor.run(
                version=version,
                role=request.simulated_party,
                prompt_key=CLAIM_DETERMINATION_PROMPT,
                payload={
                    "purpose": (
                        f"This is an internal simulation prepared for the {perspective}: how the "
                        f"{request.simulated_party} is likely to respond. It is not a determination."
                    ),
                    "facts": position,
                    "title": head.title,
                    "kind": head.kind.replace("_", " "),
                    "event_summary": head.event_summary or "(no description recorded)",
                },
                fallback=lambda head=head: record_proposal(head),
                schema_key=CLAIM_DETERMINATION_SCHEMA_KEY,
            )
            if result.degraded:
                degraded_reasons.append(f"{head.head_id}: {result.error or 'the skill step failed'}")
                heads.append(floor)
                continue
            output = hold_to_record(ClaimDeterminationOutput(**result.output), head)
            heads.append(
                SimulatedHead(
                    head_id=head.head_id,
                    head_title=head.title,
                    likely_result=output.result,
                    reasoning=output.findings,
                    likely_challenges=output.residual_risks[:20],
                    information_likely_requested=output.information_required[:20],
                    produced_by="skill",
                )
            )
        simulation = ClaimSimulation(
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            claim_id=str(claim["_id"]),
            simulated_party=request.simulated_party,  # type: ignore[arg-type]
            prepared_for=perspective,  # type: ignore[arg-type]
            heads=heads,
            board_version=bench.board_version,
            degraded=bool(degraded_reasons),
            degraded_reason="; ".join(degraded_reasons)[:600] or None,
            created_by=user_id,
        )
        await self._collection.insert_one(simulation.model_dump(by_alias=True))
        logger.info("claim simulation: claim=%s party=%s degraded=%s", claim["_id"], request.simulated_party, simulation.degraded)
        return simulation

    # -------------------------------------------------------- strategy note
    async def _note(self, claim: Dict[str, Any]) -> Optional[StrategyNote]:
        row = await self._collection.find_one({"claim_id": str(claim["_id"]), "kind": "strategy_note"})
        return StrategyNote(**row) if row else None

    async def strategy_note(self, claim: Dict[str, Any]) -> StrategyNoteResponse:
        bench = self.bench._bench_of(claim)
        return StrategyNoteResponse(
            claim_id=str(claim["_id"]),
            note=await self._note(claim),
            current_board_version=bench.board_version if bench else None,
        )

    async def _store(self, claim: Dict[str, Any], note: StrategyNote, existing: Optional[StrategyNote]) -> StrategyNoteResponse:
        note.updated_at = now_utc()
        if existing is None:
            await self._collection.insert_one(note.model_dump(by_alias=True))
        else:
            note.id = existing.id
            note.version = existing.version + 1
            result = await self._collection.update_one(
                {"_id": existing.id, "version": existing.version},
                {"$set": note.model_dump(by_alias=True, exclude={"id"})},
            )
            if not getattr(result, "matched_count", 0):
                raise HTTPException(status_code=409, detail="The strategy note changed since you loaded it.")
        bench = self.bench._bench_of(claim)
        return StrategyNoteResponse(
            claim_id=str(claim["_id"]), note=note, current_board_version=bench.board_version if bench else None
        )

    async def generate_note(self, claim: Dict[str, Any], user_id: Optional[str]) -> StrategyNoteResponse:
        """Draft the note from the board. Replaces the lists; keeps the free-text notes a person wrote."""
        bench = self.bench._require_bench(claim)
        perspective = await self.bench._perspective(claim)
        if perspective is None:
            raise HTTPException(status_code=409, detail="State which party this project acts for before drafting a strategy note.")
        existing = await self._note(claim)
        floor = record_strategy(bench)
        version, unavailable = await self._skill(claim)
        output, produced_by, reason = floor, "record_check", unavailable
        if version is not None:
            result = await self.bench.runner.executor.run(
                version=version,
                role=perspective,
                prompt_key=STRATEGY_NOTE_PROMPT,
                payload={"facts": "\n\n".join(f"{head.head_id} {head.title}\n{head_position(head)}" for head in bench.heads)},
                fallback=lambda: floor,
                schema_key=STRATEGY_NOTE_SCHEMA_KEY,
            )
            if result.degraded:
                reason = result.error or "the skill step failed"
            else:
                output, produced_by = StrategyNoteOutput(**result.output), "skill"
        note = StrategyNote(
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            claim_id=str(claim["_id"]),
            strengths=output.strengths[:40],
            weaknesses=output.weaknesses[:40],
            residual_risks=output.residual_risks[:40],
            notes=existing.notes if existing else "",
            board_version=bench.board_version,
            produced_by=produced_by,  # type: ignore[arg-type]
            degraded=produced_by != "skill",
            degraded_reason=reason if produced_by != "skill" else None,
            updated_by=user_id,
        )
        return await self._store(claim, note, existing)

    async def save_note(self, claim: Dict[str, Any], request: StrategyNoteRequest, user_id: Optional[str]) -> StrategyNoteResponse:
        existing = await self._note(claim)
        if existing is not None and existing.version != request.version:
            raise HTTPException(status_code=409, detail="The strategy note changed since you loaded it.")
        bench = self.bench._bench_of(claim)

        def _clean(items: List[str]) -> List[str]:
            return [item.strip()[:600] for item in items if item.strip()][:40]

        note = StrategyNote(
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            claim_id=str(claim["_id"]),
            strengths=_clean(request.strengths),
            weaknesses=_clean(request.weaknesses),
            residual_risks=_clean(request.residual_risks),
            notes=request.notes.strip(),
            board_version=bench.board_version if bench else None,
            produced_by="person",
            updated_by=user_id,
        )
        return await self._store(claim, note, existing)


__all__ = [
    "PRIVILEGED",
    "STRATEGY_NOTE_PROMPT",
    "ClaimPrivileged",
    "SimulationRequest",
    "SimulationsResponse",
    "StrategyNoteRequest",
    "StrategyNoteResponse",
    "record_simulation",
    "record_strategy",
]
