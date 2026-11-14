"""User Direction Agent (multi-agent workflow Phase 2).

Generates deterministic probing questions from the incoming analysis and the
context threshold gaps so the drafter states a line of action BEFORE the
strategy/draft stages. Deterministic on purpose: instant, testable, and no
LLM-invented questions. Answers merge into the drafting inputs.
"""

from __future__ import annotations

from typing import List, Optional

from ...models.letter_drafting import (
    DraftRunCreateRequest,
    IncomingLetterAnalysis,
    ProbingQuestion,
    UserDirectionAnswer,
)

MAX_QUESTIONS = 6


class UserDirectionAgent:
    @staticmethod
    def build_questions(
        analysis: Optional[IncomingLetterAnalysis],
        request: DraftRunCreateRequest,
        missing_inputs: Optional[List[str]] = None,
    ) -> List[ProbingQuestion]:
        questions: List[ProbingQuestion] = []

        def _add(question_id: str, question: str, category: str, why: str) -> None:
            if len(questions) < MAX_QUESTIONS:
                questions.append(
                    ProbingQuestion(
                        question_id=question_id,
                        question=question,
                        category=category,  # type: ignore[arg-type]
                        why=why,
                    )
                )

        if analysis and not (request.desired_position or "").strip():
            focus = (
                analysis.key_reply_points[0]
                if analysis.key_reply_points
                else (analysis.main_request or "the sender's request")
            )
            _add(
                "position",
                (
                    f"What position should the reply take on this AI-suggested reply "
                    f"consideration: {focus}?"
                    if analysis.key_reply_points
                    else f"What position should the reply take on: {focus}?"
                ),
                "position",
                "No desired position was provided; the draft cannot commit to accept/reject/reserve without it.",
            )

        if analysis and analysis.response_deadline:
            _add(
                "deadline",
                f"The incoming letter indicates a response deadline ({analysis.response_deadline}). "
                "Confirm the reply must issue before this date, or state the intended date.",
                "deadline",
                "A stated deadline drives approval urgency and workflow due date.",
            )

        if analysis:
            unfound = [
                ev.clause_number
                for ev in analysis.cited_clause_evaluations
                if ev.exists_in_contract is False
            ]
            if unfound:
                _add(
                    "clause_unfound",
                    "Cited clause(s) "
                    + ", ".join(unfound[:4])
                    + " were not found in the project's clause index. Confirm the correct "
                    "reference(s) or upload/index the missing contract volume.",
                    "clause",
                    "Replying on an unverified clause reference risks citing wording that does not exist.",
                )

        if analysis and analysis.amount_claimed:
            _add(
                "amount",
                f"The sender claims {analysis.amount_claimed}. Should the reply admit, dispute, "
                "or reserve position on this amount?",
                "amount",
                "Amounts create financial exposure; the stance must be a human decision.",
            )

        for missing in (missing_inputs or [])[:2]:
            _add(
                f"missing:{missing}",
                f"Required input missing: {missing.replace('_', ' ')}. Please provide it.",
                "missing_input",
                "Threshold inputs must be present before drafting.",
            )

        if analysis and len(analysis.key_reply_points or []) > 1:
            _add(
                "scope",
                f"AI analysis of the incoming letter suggested {len(analysis.key_reply_points)} "
                "reply considerations (advisory, not statements in the letter). "
                "Confirm the reply should address all of them, or state which to exclude.",
                "scope",
                "Locks the reply agenda before planning.",
            )

        return questions

    @staticmethod
    def format_directions(
        answers: List[UserDirectionAnswer],
        directions: Optional[str] = None,
    ) -> str:
        """Render answers into a block that merges into drafting inputs."""
        lines: List[str] = []
        for answer in answers:
            prefix = f"[{answer.question_id}] " if answer.question_id else ""
            text = answer.answer.strip()
            if text:
                lines.append(f"- {prefix}{text}")
        if directions and directions.strip():
            lines.append(f"- {directions.strip()}")
        if not lines:
            return ""
        return "User direction / line of action:\n" + "\n".join(lines)


__all__ = ["UserDirectionAgent", "MAX_QUESTIONS"]
