from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from ..config.document_processing_config import DocumentProcessingConfig
from ..models.ai_models import StrategyContextResponse, StrategyContextTimelineEntry
from ..models.letter import Letter
from .conversation_service import ConversationService
from .letter_service import LetterService
from .party_service import PartyService
from ..services.document_service import DocumentService

logger = logging.getLogger(__name__)


ROLE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "contractor": ("contractor", "consortium", "afcons", "sam india"),
    "engineer": ("engineer", "general consultant", "gc"),
    "employer": ("employer", "upmrc", "client", "owner"),
}


class StrategyContextService:
    """Builds consolidated Contractor/Engineer/Employer contexts from a letter thread."""

    def __init__(
        self,
        letter_service: Optional[LetterService] = None,
        conversation_service: Optional[ConversationService] = None,
        party_service: Optional[PartyService] = None,
    ) -> None:
        self.letter_service = letter_service
        self.conversation_service = conversation_service or ConversationService(letter_service)
        self.party_service = party_service or PartyService()
        self.config = DocumentProcessingConfig()
        self._ai_client: Optional[AsyncOpenAI] = None
        try:
            if self.config.openai_api_key:
                self._ai_client = AsyncOpenAI(
                    api_key=self.config.openai_api_key,
                    timeout=self.config.openai_timeout,
                )
        except Exception as exc:  # pragma: no cover - optional dependency
            logger.warning("OpenAI client unavailable for strategy contexts: %s", exc)
            self._ai_client = None

    async def generate_context(
        self, letter_id: str, current_user: Any
    ) -> StrategyContextResponse:
        """Consolidated per-role contexts for a letter thread.

        Everything this produces is PERSISTED onto the letter by the caller, so
        the boundary here is material influence, not display: an unauthorised
        source must be excluded before it is concatenated, before it reaches the
        synthesis prompt, and therefore before it can be stored and read by
        every later consumer. Filtering the answer afterwards would be too late.

        Two distinct authority objects, two canonical boundaries:

        * thread LETTERS - canonical row visibility via the scoped conversation
          seam. A shared `conversation_id`, a reply edge, a matching
          organisation, or the fact that the anchor letter is authorised are
          association, not authority;
        * curated DOCUMENTS - the scoped seam that applies both row visibility
          and `is_consumable`, so a quarantined, deleted or human-review
          document cannot contribute its extraction-derived text.

        `current_user` carries no default so a caller cannot omit the identity
        this is bounded by.
        """
        conversation = await self.conversation_service.get_authorized_conversation_chain(
            letter_id, current_user
        )
        if not conversation:
            return StrategyContextResponse(letter_id=letter_id)

        # Load the anchor letter so we can merge curated context documents as well
        anchor_letter = await (self.letter_service or LetterService()).get_letter(letter_id) if self.letter_service else None

        party_map = await self._load_parties(conversation)

        contexts: Dict[str, List[str]] = {
            "contractor": [],
            "engineer": [],
            "employer": [],
        }
        thread_letters: List[str] = []
        timeline: List[StrategyContextTimelineEntry] = []

        for letter in conversation:
            if not isinstance(letter, Letter):
                continue
            thread_letters.append(str(letter.id))
            role = self._infer_role(letter, party_map)
            entry = self._format_letter_entry(letter)
            if entry:
                contexts[role].append(entry)
            timeline.append(
                StrategyContextTimelineEntry(
                    letter_id=str(letter.id),
                    role=role,
                    letter_no=getattr(letter, "letter_no", None),
                    subject=letter.subject,
                    date=self._format_date(getattr(letter, "date", None) or getattr(letter, "created_at", None)),
                )
            )

        # Merge in explicitly selected context documents (if any)
        if anchor_letter:
            try:
                document_ids = getattr(anchor_letter, "context_document_ids", []) or []
                if document_ids:
                    # A curated id list is not authority: whoever stored it is
                    # not necessarily the caller, and a document's publication
                    # state can turn adverse after it was curated.
                    doc_service = DocumentService(getattr(self.letter_service, "db", None))
                    docs = await doc_service.get_documents_by_ids_in_scope(
                        document_ids,
                        current_user,
                        organization_id=(
                            str(anchor_letter.organization_id)
                            if anchor_letter.organization_id
                            else None
                        ),
                        project_id=(
                            str(anchor_letter.project_id)
                            if anchor_letter.project_id
                            else None
                        ),
                    )
                    for doc in docs:
                        entry_text = self._format_document_entry(doc)
                        role = self._infer_role_from_text(
                            " ".join(
                                filter(
                                    None,
                                    [
                                        getattr(doc, "letterNo", None),
                                        getattr(doc, "subject", None),
                                        getattr(doc, "summary", None),
                                        getattr(doc, "full_text", None),
                                    ],
                                )
                            )
                        )
                        if entry_text:
                            contexts[role].append(entry_text)
            except Exception as exc:  # pragma: no cover - defensive; do not block context creation
                logger.warning("Failed to merge context documents for strategy context: %s", exc)

        contractor_context = await self._summarize_role_context("contractor", contexts["contractor"])
        engineer_context = await self._summarize_role_context("engineer", contexts["engineer"])
        employer_context = await self._summarize_role_context("employer", contexts["employer"])

        return StrategyContextResponse(
            letter_id=letter_id,
            contractor_context=contractor_context,
            engineer_context=engineer_context,
            employer_context=employer_context,
            thread_letters=thread_letters,
            timeline=timeline,
        )

    async def _load_parties(self, letters: List[Letter]) -> Dict[str, Any]:
        party_ids = {
            pid
            for letter in letters
            for pid in (getattr(letter, "from_party_id", None), getattr(letter, "to_party_id", None))
            if pid
        }
        party_map: Dict[str, Any] = {}
        for pid in party_ids:
            try:
                party = await self.party_service.get_party_by_id(pid)
                if party:
                    party_map[pid] = party
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Unable to load party %s: %s", pid, exc)
        return party_map

    def _infer_role(self, letter: Letter, party_map: Dict[str, Any]) -> str:
        raw_text = " ".join(
            filter(
                None,
                [
                    getattr(letter, "recipient", None),
                    getattr(letter, "title", None),
                    getattr(letter, "subject", None),
                    getattr(letter, "content", None),
                ],
            )
        ).lower()

        from_party = party_map.get(getattr(letter, "from_party_id", None))
        if from_party:
            raw_text = f"{from_party.name} {raw_text}".lower()

        for role, hints in ROLE_KEYWORDS.items():
            if any(keyword in raw_text for keyword in hints):
                return role

        # Default heuristics: assume contractor authored the letter
        return "contractor"

    def _infer_role_from_text(self, text: str) -> str:
        lowered = (text or "").lower()
        for role, hints in ROLE_KEYWORDS.items():
            if any(keyword in lowered for keyword in hints):
                return role
        # Heuristic: letter numbers with "-e" or "e0" often indicate engineer; "-c" contractor; "-emp" employer
        if "-e" in lowered or " e0" in lowered:
            return "engineer"
        if "-emp" in lowered or " employer" in lowered:
            return "employer"
        return "contractor"

    @staticmethod
    def _format_letter_entry(letter: Letter) -> str:
        lines: List[str] = []
        date_value = getattr(letter, "date", None) or getattr(letter, "created_at", None)
        date_label = StrategyContextService._format_date(date_value)
        letter_no = getattr(letter, "letter_no", None) or getattr(letter, "letterNo", None)
        subject = letter.subject or ""
        # Include sender/recipient cues when available
        sender = getattr(letter, "from_party_id", None) or getattr(letter, "created_by", None)
        recipient = getattr(letter, "to_party_id", None) or getattr(letter, "assigned_to", None)
        header_parts = [part for part in [date_label, letter_no, subject] if part]
        if sender or recipient:
            header_parts.append(
                " -> ".join(filter(None, [str(sender) if sender else None, str(recipient) if recipient else None]))
            )
        if header_parts:
            lines.append(" - ".join(header_parts))
        content = getattr(letter, "content", "") or ""
        if content:
            lines.append(content.strip())
        return "\n".join(lines).strip()

    @staticmethod
    def _format_document_entry(doc: Any) -> str:
        try:
            date_value = getattr(doc, "date", None)
            date_label = StrategyContextService._format_date(date_value)
        except Exception:
            date_label = None
        letter_no = getattr(doc, "letterNo", None) or getattr(doc, "letter_no", None)
        subject = getattr(doc, "subject", None) or getattr(doc, "title", None)
        summary = getattr(doc, "summary", None) or getattr(doc, "full_text", None) or getattr(doc, "ocrText", None)
        header_parts = [part for part in [date_label, letter_no, subject] if part]
        lines: List[str] = []
        if header_parts:
            lines.append(" - ".join(header_parts))
        if summary:
            lines.append(str(summary).strip())
        return "\n".join(lines).strip()

    @staticmethod
    def _merge_context(chunks: List[str]) -> Optional[str]:
        merged = "\n\n".join(chunk for chunk in chunks if chunk).strip()
        return merged or None

    async def _summarize_role_context(self, role: str, chunks: List[str]) -> Optional[str]:
        """
        Use AI (if configured) to produce a cohesive narrative per role.
        Falls back to simple merge when AI is unavailable.
        """
        if not chunks:
            return None

        if not self._ai_client:
            return self._merge_context(chunks)

        try:
            # Keep prompt size reasonable
            joined = "\n\n---\n\n".join(chunks)
            max_chars = 12000
            trimmed = joined[:max_chars]
            instructions = (
                "You will receive multiple letters exchanged in a thread. "
                "Write a concise, cohesive narrative summary for the specified role, "
                "capturing chronology, key asks, commitments, risks, and tone. "
                "Reference letter numbers/dates where present. Do not invent facts."
            )
            user_prompt = (
                f"Role: {role}\n"
                f"Letters (oldest to newest, separated by ---):\n{trimmed}\n\n"
                "Produce a single consolidated summary/story in paragraph form."
            )
            response = await self._ai_client.chat.completions.create(
                model=self.config.openai_model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=min(self.config.max_output_tokens or 600, 900),
            )
            choice = response.choices[0].message.content if response.choices else None
            if isinstance(choice, list):
                choice = " ".join([str(part) for part in choice if part])
            text = (choice or "").strip()
            return text or self._merge_context(chunks)
        except Exception as exc:
            logger.warning("AI context summarization failed for role %s: %s", role, exc)
            return self._merge_context(chunks)

    @staticmethod
    def _format_date(value: Optional[datetime]) -> Optional[str]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        return str(value)
