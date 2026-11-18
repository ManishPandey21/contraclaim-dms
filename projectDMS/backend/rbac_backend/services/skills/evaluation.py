"""Server-side evaluation of a skill version against its golden set (4B-41, D-21).

An evaluation run is created here, never typed in by a client:

- the suite is read from the restricted evaluation store (``SKILL_EVAL_SUITE_DIR``,
  outside the repository; AQ-10) and identified by the hash of its content;
- the run records the suite hash, the pack's content hash, its policy hash, the
  model, the prompt and its version, the configuration and the session user
  (``recorded_by``), none of which a request can supply;
- the threshold is server configuration (``minimum_eval_threshold``: 0.85 at
  least, whatever is configured or asked for);
- a run is written once when it is complete and has no update path. Approvals
  and publication name a run by its id (``eval_run_id``); a change to the pack,
  its policy, the suite or the model makes the run stale, and a new run is needed.

Gates (plan section 7). Hard: no figure that the matter does not contain; no
notice period the matter does not state; no clause the matter does not contain;
nothing marked privileged in the matter appears in the output; every output
valid against the pack's schema. Scored: element-state agreement with the
adjudicated labels (per pack, and per element type at 0.75), classification
agreement, degraded-step rate at most 5 %.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ...core.config import settings
from ...models.skill import SkillEvalReport, SkillVersion
from ...utils.datetime import now_utc
from .approvals import manifest_policy_hash, minimum_eval_threshold
from .executor import ExecutionResult, canonical_hash

SKILL_EVAL_RUNS = "skill_eval_runs"

ELEMENT_TYPE_FLOOR = 0.75
CLASSIFICATION_FLOOR = 0.85
MAX_DEGRADED_RATE = 0.05
DEFAULT_MAX_MODEL_CALLS = 60

_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_CLAUSE = re.compile(r"\b(?:sub-?\s*)?clause\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
_NOTICE_DAYS = re.compile(r"(\d+)\s*(?:calendar\s+|working\s+)?days?\b", re.IGNORECASE)


def _refusal(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "user_message": message, "detail": message})


# --------------------------------------------------------------------------- #
# Suites
# --------------------------------------------------------------------------- #
class EvalMatterRecord(BaseModel):
    """What the matter itself contains, for the hard gates."""

    model_config = ConfigDict(extra="forbid")

    clauses: List[str] = Field(default_factory=list)
    #: Notice periods the contract in this matter states, in days. Any other is assumed.
    notice_days: List[int] = Field(default_factory=list)
    #: Strings from the matter's privileged material. None may appear in an output.
    privileged_markers: List[str] = Field(default_factory=list)


class EvalClassificationLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    expected: str


class EvalLabels(BaseModel):
    """Adjudicated labels: element states by element, and one classification."""

    model_config = ConfigDict(extra="forbid")

    elements: Dict[str, str] = Field(default_factory=dict)
    classification: Optional[EvalClassificationLabel] = None
    information_required: List[str] = Field(default_factory=list)


class EvalMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matter_id: str = Field(..., min_length=1, max_length=80)
    role: str
    payload: Dict[str, str]
    record: EvalMatterRecord = Field(default_factory=EvalMatterRecord)
    labels: EvalLabels
    composition: List[str] = Field(default_factory=list)


class EvalSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_key: str = Field(..., min_length=1, max_length=120)
    skill_key: str
    prompt_key: str
    matters: List[EvalMatter] = Field(..., min_length=1)


def suite_hash(suite: EvalSuite) -> str:
    return canonical_hash(suite.model_dump(mode="json"))


def suite_dir() -> Optional[Path]:
    value = str(getattr(settings, "SKILL_EVAL_SUITE_DIR", "") or "").strip()
    return Path(value) if value else None


def load_suite(skill_key: str, directory: Optional[Path] = None) -> EvalSuite:
    """The golden set for a pack, from the evaluation store. One file per pack: ``<skill_key>.json``."""
    base = directory if directory is not None else suite_dir()
    if base is None:
        raise _refusal(
            409, "eval_suite_store_not_configured",
            "No evaluation store is configured on this server, so no evaluation can be run.",
        )
    path = (base / f"{skill_key}.json").resolve()
    if path.parent != base.resolve() or not path.is_file():
        raise _refusal(409, "eval_suite_missing", f"There is no golden set for {skill_key} in the evaluation store.")
    try:
        suite = EvalSuite.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as exc:
        raise _refusal(409, "eval_suite_invalid", f"The golden set for {skill_key} cannot be read: {str(exc)[:200]}")
    if suite.skill_key != skill_key:
        raise _refusal(409, "eval_suite_invalid", f"The golden set in {path.name} is for {suite.skill_key}.")
    return suite


# --------------------------------------------------------------------------- #
# Scoring (pure)
# --------------------------------------------------------------------------- #
class MatterResult(BaseModel):
    matter_id: str
    degraded: bool = False
    schema_valid: bool = True
    error: Optional[str] = None
    #: Hard-gate failures for this matter, each "gate: detail".
    hard_failures: List[str] = Field(default_factory=list)
    #: element -> [expected, actual]
    elements: Dict[str, List[Optional[str]]] = Field(default_factory=dict)
    classification: Optional[List[Optional[str]]] = None
    output_hash: Optional[str] = None


def _strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    return []


def _numbers(texts: List[str]) -> set:
    return {match.replace(",", "") for text in texts for match in _NUMBER.findall(text)}


def _clause_refs(output: Dict[str, Any], texts: List[str]) -> set:
    refs = {str(ref).strip() for ref in output.get("clause_refs") or [] if str(ref).strip()}
    refs |= {match for text in texts for match in _CLAUSE.findall(text)}
    return refs


def _actual(output: Dict[str, Any], field: str) -> Optional[str]:
    value = output.get(field)
    if isinstance(value, dict):
        value = value.get("state")
    return None if value is None else str(value)


def score_matter(matter: EvalMatter, result: ExecutionResult) -> MatterResult:
    """The hard gates and label agreement for one matter's output."""
    output = result.output or {}
    texts = _strings(output)
    found = MatterResult(
        matter_id=matter.matter_id,
        degraded=result.degraded,
        schema_valid=not (result.error or "").startswith("ValidationError"),
        error=result.error,
        output_hash=canonical_hash(output),
    )
    if not found.schema_valid:
        found.hard_failures.append("schema: the output does not match the pack's schema")
    allowed = _numbers(_strings(matter.payload))
    invented = sorted(_numbers(texts) - allowed)
    if invented:
        found.hard_failures.append("figures: not in the matter: " + ", ".join(invented[:10]))
    clauses = {str(ref).strip() for ref in matter.record.clauses}
    absent = sorted(_clause_refs(output, texts) - clauses)
    if absent:
        found.hard_failures.append("clauses: cited but not in the matter: " + ", ".join(absent[:10]))
    assumed = sorted(
        {
            days
            for text in texts
            for sentence in re.split(r"(?<=[.;])\s+", text)
            if "notice" in sentence.lower()
            for days in _NOTICE_DAYS.findall(sentence)
            if int(days) not in set(matter.record.notice_days)
        }
    )
    if assumed:
        found.hard_failures.append("notice: periods the contract does not state: " + ", ".join(assumed))
    lowered = " ".join(texts).lower()
    leaked = [marker for marker in matter.record.privileged_markers if marker.strip() and marker.lower() in lowered]
    if leaked:
        found.hard_failures.append(f"privileged: {len(leaked)} privileged passage(s) in the output")
    for element, expected in matter.labels.elements.items():
        found.elements[element] = [expected, _actual(output, element)]
    if matter.labels.classification is not None:
        label = matter.labels.classification
        found.classification = [label.expected, _actual(output, label.field)]
    return found


class EvalScores(BaseModel):
    element_agreement: float = 0.0
    element_agreement_by_type: Dict[str, float] = Field(default_factory=dict)
    classification_agreement: Optional[float] = None
    degraded_rate: float = 0.0
    schema_validity: float = 0.0


def _rate(hits: int, total: int) -> float:
    return round(hits / total, 6) if total else 0.0


def aggregate(results: List[MatterResult], threshold: float) -> Tuple[EvalScores, List[str], bool]:
    """Scores, the failing gates (named), and whether the run passed."""
    pairs = [(element, pair) for result in results for element, pair in result.elements.items()]
    by_type: Dict[str, List[bool]] = {}
    for element, (expected, actual) in pairs:
        by_type.setdefault(element, []).append(expected == actual)
    classified = [result.classification for result in results if result.classification is not None]
    scores = EvalScores(
        element_agreement=_rate(sum(expected == actual for _e, (expected, actual) in pairs), len(pairs)),
        element_agreement_by_type={key: _rate(sum(hits), len(hits)) for key, hits in sorted(by_type.items())},
        classification_agreement=(
            _rate(sum(pair[0] == pair[1] for pair in classified), len(classified)) if classified else None
        ),
        degraded_rate=_rate(sum(result.degraded for result in results), len(results)),
        schema_validity=_rate(sum(result.schema_valid for result in results), len(results)),
    )
    failures: List[str] = []
    hard = [f"{result.matter_id}: {failure}" for result in results for failure in result.hard_failures]
    failures.extend(f"hard gate - {item}" for item in hard)
    if not pairs:
        failures.append("no labelled elements were scored")
    elif scores.element_agreement < threshold:
        failures.append(f"element agreement {scores.element_agreement:.2f} is below {threshold:.2f}")
    for element, value in scores.element_agreement_by_type.items():
        if value < ELEMENT_TYPE_FLOOR:
            failures.append(f"element agreement on {element} {value:.2f} is below {ELEMENT_TYPE_FLOOR:.2f}")
    if scores.classification_agreement is not None and scores.classification_agreement < CLASSIFICATION_FLOOR:
        failures.append(
            f"classification agreement {scores.classification_agreement:.2f} is below {CLASSIFICATION_FLOOR:.2f}"
        )
    if scores.degraded_rate > MAX_DEGRADED_RATE:
        failures.append(f"degraded-step rate {scores.degraded_rate:.2f} is above {MAX_DEGRADED_RATE:.2f}")
    return scores, failures, not failures


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
class EvalRunRequest(BaseModel):
    """Nothing about the run's identity, threshold or evaluator can be supplied."""

    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = Field(default=None, max_length=1000)


class EvalRunCost(BaseModel):
    model_calls: int = 0
    prompt_characters: int = 0
    output_characters: int = 0


class SkillEvalRun(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    skill_key: str
    version: str
    content_hash: str
    policy_hash: str
    suite_key: str
    suite_hash: str
    model: str
    prompt_key: str
    prompt_version: Optional[int] = None
    configuration: Dict[str, Any] = Field(default_factory=dict)
    recorded_by: str
    threshold: float
    case_count: int
    passed: bool
    failures: List[str] = Field(default_factory=list)
    scores: EvalScores = Field(default_factory=EvalScores)
    matters: List[MatterResult] = Field(default_factory=list)
    cost: EvalRunCost = Field(default_factory=EvalRunCost)
    note: Optional[str] = None
    started_at: datetime = Field(default_factory=now_utc)
    completed_at: Optional[datetime] = None

    def report(self) -> SkillEvalReport:
        """The evaluation result approvals and publication bind to.

        The suite string names the suite, its hash and the run, so an approval
        given against one run does not count for another.
        """
        return SkillEvalReport(
            suite=f"{self.suite_key}@{self.suite_hash[:16]}#run:{self.id}",
            score=self.scores.element_agreement if self.passed else 0.0,
            threshold=self.threshold,
            case_count=self.case_count,
            recorded_by=self.recorded_by,
            recorded_at=self.completed_at or self.started_at,
        )


#: Runs one matter: (version, role, prompt_key, payload) -> execution result.
MatterExecutor = Callable[[SkillVersion, str, str, Dict[str, str]], Awaitable[ExecutionResult]]


def max_model_calls() -> int:
    try:
        return max(1, int(getattr(settings, "SKILL_EVAL_MAX_MODEL_CALLS", DEFAULT_MAX_MODEL_CALLS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_MODEL_CALLS


@dataclass
class EvalBinding:
    model: str
    prompt_version: Optional[int]


class SkillEvalRunService:
    def __init__(
        self,
        db: Any,
        execute: Optional[MatterExecutor] = None,
        model_name: Optional[str] = None,
        suite_directory: Optional[Path] = None,
    ) -> None:
        from .executor import skill_model_name

        self.db = db
        self._execute = execute
        self.model_name = model_name or skill_model_name()
        self.suite_directory = suite_directory

    async def _default_execute(
        self, version: SkillVersion, role: str, prompt_key: str, payload: Dict[str, str]
    ) -> ExecutionResult:
        from ..letter_drafting.prompts import PromptRegistry
        from .executor import SkillExecutor
        from .schemas import get_schema

        schema = get_schema(version.manifest.output_schema_key)
        if schema is None:
            raise _refusal(409, "eval_schema_unknown", f"{version.skill_key} names no known output schema.")
        executor = SkillExecutor(PromptRegistry(self.db), model_name=self.model_name)
        return await executor.run(
            version=version, role=role, prompt_key=prompt_key, payload=payload, fallback=lambda: schema()
        )

    def _suite(self, skill_key: str) -> EvalSuite:
        return load_suite(skill_key, self.suite_directory)

    async def run(self, version: SkillVersion, request: EvalRunRequest, recorded_by: str) -> SkillEvalRun:
        if not recorded_by:
            raise HTTPException(status_code=403, detail="An evaluation needs an identified user")
        if version.status != "draft":
            raise _refusal(409, "eval_version_not_draft", f"{version.skill_key} {version.version} is {version.status}.")
        suite = self._suite(version.skill_key)
        budget = max_model_calls()
        if len(suite.matters) > budget:
            raise _refusal(
                409, "eval_budget_exceeded",
                f"The golden set has {len(suite.matters)} matters; the configured budget is {budget} model calls a run. "
                "Running it needs the budget raised with approval (AQ-10).",
            )
        execute = self._execute or self._default_execute
        threshold = minimum_eval_threshold()
        results: List[MatterResult] = []
        cost = EvalRunCost()
        prompt_version: Optional[int] = None
        for matter in suite.matters:
            executed = await execute(version, matter.role, suite.prompt_key, dict(matter.payload))
            prompt_version = executed.prompt_version
            cost.model_calls += 1
            cost.prompt_characters += sum(len(value) for value in matter.payload.values())
            cost.output_characters += len(json.dumps(executed.output or {}, default=str))
            results.append(score_matter(matter, executed))
        scores, failures, passed = aggregate(results, threshold)
        run = SkillEvalRun(
            skill_key=version.skill_key,
            version=version.version,
            content_hash=version.content_hash,
            policy_hash=manifest_policy_hash(version.manifest),
            suite_key=suite.suite_key,
            suite_hash=suite_hash(suite),
            model=self.model_name,
            prompt_key=suite.prompt_key,
            prompt_version=prompt_version,
            configuration={
                "threshold": threshold,
                "element_type_floor": ELEMENT_TYPE_FLOOR,
                "classification_floor": CLASSIFICATION_FLOOR,
                "max_degraded_rate": MAX_DEGRADED_RATE,
                "max_model_calls": budget,
            },
            recorded_by=recorded_by,
            threshold=threshold,
            case_count=len(results),
            passed=passed,
            failures=failures,
            scores=scores,
            matters=results,
            cost=cost,
            note=request.note,
        )
        run.completed_at = now_utc()
        # Written once, complete. Nothing updates a run.
        await self.db[SKILL_EVAL_RUNS].insert_one(run.model_dump(by_alias=True, mode="python"))
        return run

    async def get(self, run_id: str) -> Optional[SkillEvalRun]:
        doc = await self.db[SKILL_EVAL_RUNS].find_one({"_id": run_id})
        return SkillEvalRun(**doc) if doc else None

    async def list(self, skill_key: str, version: str) -> List[SkillEvalRun]:
        docs = await self.db[SKILL_EVAL_RUNS].find({"skill_key": skill_key, "version": version}).to_list(length=200)
        runs = [SkillEvalRun(**doc) for doc in docs]
        runs.sort(key=lambda item: item.started_at, reverse=True)
        return runs

    async def report_for(self, version: SkillVersion, run_id: str) -> Tuple[SkillEvalRun, SkillEvalReport]:
        """The run an approval or a publication names, checked as still describing this version.

        A run counts only for the version it evaluated, while the pack's text and
        policy, the golden set and the model are what they were when it ran.
        """
        run = await self.get(run_id)
        if run is None or run.skill_key != version.skill_key or run.version != version.version:
            raise _refusal(404, "eval_run_not_found", "No such evaluation run for this version.")
        stale: List[str] = []
        if run.content_hash != version.content_hash:
            stale.append("the pack's text")
        if run.policy_hash != manifest_policy_hash(version.manifest):
            stale.append("its policy")
        if run.model != self.model_name:
            stale.append("the model")
        try:
            current = suite_hash(self._suite(version.skill_key))
        except HTTPException:
            current = None
        if current != run.suite_hash:
            stale.append("the golden set")
        if stale:
            raise _refusal(
                409, "eval_run_stale",
                "Since this evaluation ran, " + ", ".join(stale) + " changed. Run the evaluation again.",
            )
        return run, run.report()


__all__ = [
    "EvalRunRequest",
    "EvalSuite",
    "SKILL_EVAL_RUNS",
    "SkillEvalRun",
    "SkillEvalRunService",
    "aggregate",
    "load_suite",
    "score_matter",
    "suite_hash",
]
