from __future__ import annotations

from datetime import datetime, timezone
from textwrap import shorten
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...models.letter import Letter
from ...models.letter_drafting import DraftContextBundle, DraftRunCreateRequest, SourceEvidence
from ...models.contract_document import CurrentState
from ...services.contract_scope_resolver import (
    ProjectEvidenceUniverse,
    resolve_authorized_project_universe,
)
from ...services.contract_service import ContractService
from ...services.conversation_service import ConversationService
from ...services.document_service import DocumentService
from ...services.falkor_graph_service import FalkorGraphService, normalize_letter_code


def condense_text(value: Optional[str], width: int = 600) -> Optional[str]:
    if not value:
        return None
    normalized = " ".join(str(value).split())
    if not normalized:
        return None
    if len(normalized) <= width:
        return normalized
    return shorten(normalized, width=width, placeholder="...")


from ..publication_policy import consumable_summary, consumable_text


class DraftContextBuilder:
    """Builds a scoped v2 context bundle and deterministic source ledger."""

    def __init__(
        self,
        document_service: DocumentService,
        conversation_service: ConversationService,
        contract_service: Optional[ContractService] = None,
        graph_service: Optional[FalkorGraphService] = None,
        db: Any = None,
    ) -> None:
        self.document_service = document_service
        self.conversation_service = conversation_service
        # Retained for injection compatibility only. It is NOT an evidence
        # path: generic contract search resolves no applicability, no
        # projection currency and no positive publication authority, so it may
        # not feed a DraftRun. Contract evidence comes from the canonical
        # universe resolved in `_contract_evidence_universe`.
        self.contract_service = contract_service or ContractService()
        self.graph_service = graph_service or FalkorGraphService()
        # Raw handle for structured clause records + register evidence.
        self.db = db

    async def build(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        current_user: Any,
    ) -> Tuple[DraftContextBundle, List[SourceEvidence], List[str]]:
        warnings: List[str] = []
        org_id = str(getattr(letter, "organization_id", "") or "")
        project_id = str(getattr(letter, "project_id", "") or "")
        current_materials = self._current_materials(letter, request)
        sources: List[SourceEvidence] = []

        for idx, material in enumerate(current_materials, start=1):
            sources.append(
                SourceEvidence(
                    source_id=f"current:{idx}",
                    source_type="current_input",
                    allowed_use="fact",
                    organization_id=org_id,
                    project_id=project_id,
                    label=f"Current material {idx}",
                    text=material,
                    snippet=condense_text(material, 280),
                )
            )

        selected_document_ids, document_sources, comment_lines = await self._document_sources(
            letter, request, org_id, project_id, warnings
        )
        sources.extend(document_sources)

        # Contract evidence. ONE canonical eligible universe, resolved once and
        # shared, so no clause source can end up searching a wider population
        # than another. The legacy `ContractService.search_contracts` supplement
        # was REMOVED rather than fenced: generic search resolves no
        # applicability, no projection currency and no positive publication
        # authority, and it spends its candidate limit before any of them
        # exist, so there is no point in its pipeline where a fence would work.
        contract_universe = await self._contract_evidence_universe(
            org_id, project_id, current_user, warnings
        )
        clause_record_sources = await self._clause_record_sources(
            letter, request, org_id, project_id, contract_universe, warnings
        )
        sources.extend(clause_record_sources)

        register_sources = await self._register_sources(org_id, project_id, warnings)
        sources.extend(register_sources)

        prior_ids, prior_sources = await self._prior_correspondence_sources(
            letter, request, current_user, org_id, project_id, warnings
        )
        sources.extend(prior_sources)
        sources.extend(self._previous_position_sources(prior_sources, org_id, project_id))

        graph_codes, graph_sources = await self._graph_sources(letter, request, org_id, project_id, warnings)
        sources.extend(graph_sources)

        threshold_inputs = {
            "sender_profile": bool(request.role or getattr(letter, "strategy_role", None)),
            "letter_purpose": bool(request.subject or letter.subject),
            "intended_recipient": bool(request.recipient or letter.recipient),
            "key_issue_or_event": bool(current_materials),
            "main_factual_basis": bool(current_materials or document_sources or prior_sources),
        }

        bundle = DraftContextBundle(
            active_workspace={
                "organization_id": org_id or None,
                "project_id": project_id or None,
                "letter_id": str(letter.id),
                "letter_no": getattr(letter, "letter_no", None),
            },
            current_materials=current_materials,
            selected_document_ids=selected_document_ids,
            prior_correspondence_ids=prior_ids,
            graph_thread_codes=graph_codes,
            comments=comment_lines,
            threshold_inputs=threshold_inputs,
        )
        return bundle, self._dedupe_sources(sources), warnings

    def _current_materials(self, letter: Letter, request: DraftRunCreateRequest) -> List[str]:
        materials: List[str] = []
        for value in [
            request.purpose,
            request.requirements,
            request.points,
            request.desired_position,
            request.required_action,
            request.background_facts,
            request.trigger_event,
            "\n".join(request.clauses_to_consider),
            letter.content,
            getattr(letter, "contractor_context", None),
            getattr(letter, "engineer_context", None),
            getattr(letter, "employer_context", None),
        ]:
            condensed = condense_text(value, 1600)
            if condensed and condensed not in materials:
                materials.append(condensed)
        return materials

    async def _document_sources(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        org_id: str,
        project_id: str,
        warnings: List[str],
    ) -> Tuple[List[str], List[SourceEvidence], List[str]]:
        doc_ids = list(dict.fromkeys([str(x) for x in request.document_ids if x]))
        if not doc_ids:
            doc_ids = [str(x) for x in (getattr(letter, "context_document_ids", []) or []) if x]
        docs = await self.document_service.get_documents_by_ids(doc_ids) if doc_ids else []
        selected_ids: List[str] = []
        sources: List[SourceEvidence] = []
        comments: List[str] = []
        for doc in docs:
            doc_id = str(getattr(doc, "id", "") or "")
            same_org = not org_id or str(getattr(doc, "organization_id", "")) == org_id
            same_project = not project_id or str(getattr(doc, "project_id", "") or "") == project_id
            if not same_org or not same_project:
                warnings.append(f"Skipped out-of-workspace document {doc_id}")
                continue
            selected_ids.append(doc_id)
            # Extraction-controlled content, so it goes through the
            # publication policy. This package never imported the policy at
            # all, so a document in human review or a confirmed duplicate had
            # its unverified OCR text injected straight into the drafting
            # prompt. `subject` stays ungated - it is filing metadata, not
            # extracted body text, so a blocked document remains identifiable.
            text = condense_text(
                consumable_summary(doc)
                or consumable_text(doc)
                or getattr(doc, "subject", None),
                1000,
            )
            sources.append(
                SourceEvidence(
                    source_id=f"document:{doc_id}",
                    source_type="context_document",
                    allowed_use="fact",
                    organization_id=org_id,
                    project_id=project_id,
                    label=getattr(doc, "subject", None) or getattr(doc, "letterNo", None) or "Context document",
                    text=text,
                    snippet=condense_text(text, 280),
                    document_id=doc_id,
                    metadata={
                        "letter_no": getattr(doc, "letterNo", None),
                        "upload_type": getattr(doc, "uploadType", None),
                    },
                )
            )
            try:
                for comment in (await self.document_service.get_comments(doc_id))[-5:]:
                    text_value = condense_text(comment.get("text"), 400) if isinstance(comment, dict) else None
                    if text_value:
                        comments.append(text_value)
                        sources.append(
                            SourceEvidence(
                                source_id=f"comment:{doc_id}:{len(comments)}",
                                source_type="comment",
                                allowed_use="comment",
                                organization_id=org_id,
                                project_id=project_id,
                                label="Document comment",
                                text=text_value,
                                snippet=condense_text(text_value, 200),
                                document_id=doc_id,
                            )
                        )
            except Exception as exc:
                warnings.append(f"Unable to load comments for {doc_id}: {exc}")
        return selected_ids, sources, comments

    async def _contract_evidence_universe(
        self,
        org_id: str,
        project_id: str,
        current_user: Any,
        warnings: List[str],
    ) -> Optional[ProjectEvidenceUniverse]:
        """Resolve the canonical eligible universe once, for every clause source.

        Drafting does not decide what governs a contract; it asks. The answer is
        positive (applicable at the query mode, canonical Document positively
        resolvable, publication-consumable, projection-current) and it is
        resolved for THIS ACTOR, not for the anchor letter's workspace.

        ``None`` means the question could not be answered, which is different
        from "nothing applies". Both produce zero contract evidence, and neither
        is allowed to widen into generic search - falling back is exactly the
        door the Contract Master model closes.

        Query mode is ``CurrentState``: a drafting run is written now and cites
        what governs now. ``Historical`` is deliberately not inferred from a
        letter date, because an implicit "as at" would manufacture legal
        evidence for a date nobody asked about.
        """
        if self.db is None or not org_id or not project_id:
            return None
        try:
            return await resolve_authorized_project_universe(
                self.db,
                current_user,
                organization_id=org_id,
                project_id=project_id,
                mode=CurrentState(),
            )
        except Exception as exc:
            warnings.append(f"Contract evidence unavailable: {exc}")
            return None

    async def _clause_record_sources(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        org_id: str,
        project_id: str,
        universe: Optional[ProjectEvidenceUniverse],
        warnings: List[str],
    ) -> List[SourceEvidence]:
        """Structured clause records (contract_clauses) - the clause evidence.

        A clause row's ``org_id``/``project_id`` are INGEST PROVENANCE: they say
        where the row came from, never that the instrument legally governs
        anything. They used to be the whole filter, so a mis-stamped row
        authorised itself, and ``is_authorised_for_ai`` did not help - it is
        written once at clause-index time from clause quality and is never
        revisited when the parent document's authority changes.

        The canonical eligible set is therefore part of the QUERY, not a filter
        applied to its result. That ordering is the point: the previous code
        took the first sixty rows and only then subtracted blocked documents, so
        sixty ineligible rows could starve the applicable one before authority
        was ever consulted.

        ``blocked_document_ids`` is gone from this path rather than kept beside
        the universe. It fails OPEN on an identifier it cannot resolve, and a
        second predicate that looks like authority is how the real one stops
        being read.
        """
        if self.db is None or not org_id or not project_id:
            return []
        if universe is None:
            # Eligibility could not be resolved. There is no partial answer to
            # give and no broader query that would be safer.
            return []
        eligible = sorted(universe.eligible_document_ids)
        if not eligible:
            # Valid empty. Nothing applies to this contract, which is an answer.
            return []
        query_text = " ".join(
            part
            for part in [
                request.subject or letter.subject,
                request.purpose,
                request.points,
                " ".join(request.clauses_to_consider),
            ]
            if part
        ).lower()
        terms = {t for t in query_text.split() if len(t) > 3}
        try:
            cursor = self.db.contract_clauses.find(
                {
                    "org_id": org_id,
                    "project_id": project_id,
                    "is_current": True,
                    "is_authorised_for_ai": True,
                    "document_id": {"$in": eligible},
                }
            ).limit(60)
            records = [doc async for doc in cursor]
        except Exception as exc:
            warnings.append(f"Clause record retrieval skipped: {exc}")
            return []

        scored: List[tuple[float, dict]] = []
        for record in records:
            haystack = " ".join(
                str(part or "")
                for part in (
                    record.get("cleaned_text"),
                    record.get("clause_title"),
                    record.get("clause_no"),
                )
            ).lower()
            hits = sum(1 for term in terms if term in haystack)
            requested = any(
                record.get("clause_no") == wanted.strip()
                for wanted in request.clauses_to_consider
            )
            score = hits / max(len(terms), 1) + (1.0 if requested else 0.0)
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        sources: List[SourceEvidence] = []
        for score, record in scored[:6]:
            text = condense_text(record.get("cleaned_text"), 1000)
            clause_no = record.get("clause_no")
            pages = [p for p in [record.get("page_start"), record.get("page_end")] if p]
            sources.append(
                SourceEvidence(
                    source_id=f"clause_record:{record.get('clause_uid')}",
                    source_type="contract_clause",
                    allowed_use="clause",
                    organization_id=org_id,
                    project_id=project_id,
                    label=f"Clause {clause_no or ''} {record.get('clause_title') or ''}".strip(),
                    text=text,
                    snippet=condense_text(text, 300),
                    document_id=str(record.get("document_id") or "") or None,
                    clause_number=clause_no,
                    clause_title=record.get("clause_title"),
                    page_numbers=sorted(set(int(p) for p in pages)),
                    score=round(score, 3),
                    metadata={
                        "clause_uid": record.get("clause_uid"),
                        "document_type": record.get("document_type"),
                        "structured": True,
                    },
                )
            )
        return sources

    async def _register_sources(
        self, org_id: str, project_id: str, warnings: List[str]
    ) -> List[SourceEvidence]:
        """Key dates / variation / bank-guarantee register rows as fact evidence."""
        if self.db is None or not org_id or not project_id:
            return []
        scope = {"organization_id": org_id, "project_id": project_id}
        sources: List[SourceEvidence] = []

        async def _rows(collection: str, sort_field: str) -> List[dict]:
            try:
                cursor = self.db[collection].find(scope).sort(sort_field, -1).limit(5)
                return [doc async for doc in cursor]
            except Exception as exc:
                warnings.append(f"Register {collection} unavailable: {exc}")
                return []

        for row in await _rows("key_date_milestones", "current_approved_key_date"):
            date = self._date_label(
                row.get("current_approved_key_date") or row.get("original_planned_key_date")
            )
            text = " — ".join(
                str(part)
                for part in [row.get("title"), date, condense_text(row.get("remarks"), 200)]
                if part
            )
            sources.append(
                SourceEvidence(
                    source_id=f"register:key_date:{row.get('_id')}",
                    source_type="context_document",
                    allowed_use="fact",
                    organization_id=org_id,
                    project_id=project_id,
                    label=f"Key date: {row.get('title') or row.get('milestone_ref') or 'milestone'}",
                    text=text,
                    snippet=condense_text(text, 240),
                    metadata={"register": "key_dates", "date": date},
                )
            )
        for row in await _rows("variations", "created_at"):
            text = " — ".join(
                str(part)
                for part in [
                    row.get("variation_number"),
                    condense_text(row.get("description"), 240),
                    f"submitted {row.get('submitted_amount')}" if row.get("submitted_amount") else None,
                    f"approved {row.get('approved_amount')}" if row.get("approved_amount") else None,
                    row.get("letter_reference"),
                ]
                if part
            )
            sources.append(
                SourceEvidence(
                    source_id=f"register:variation:{row.get('_id')}",
                    source_type="context_document",
                    allowed_use="fact",
                    organization_id=org_id,
                    project_id=project_id,
                    label=f"Variation {row.get('variation_number') or ''}".strip(),
                    text=text,
                    snippet=condense_text(text, 240),
                    metadata={"register": "variations"},
                )
            )
        for row in await _rows("bank_guarantees", "bg_expiry_date"):
            expiry = self._date_label(row.get("bg_expiry_date"))
            text = " — ".join(
                str(part)
                for part in [
                    row.get("bg_number"),
                    row.get("bg_type"),
                    f"amount {row.get('bg_amount')} {row.get('currency') or ''}".strip()
                    if row.get("bg_amount")
                    else None,
                    f"expires {expiry}" if expiry else None,
                    row.get("bg_status"),
                ]
                if part
            )
            sources.append(
                SourceEvidence(
                    source_id=f"register:bg:{row.get('_id')}",
                    source_type="context_document",
                    allowed_use="fact",
                    organization_id=org_id,
                    project_id=project_id,
                    label=f"Bank guarantee {row.get('bg_number') or ''}".strip(),
                    text=text,
                    snippet=condense_text(text, 240),
                    metadata={"register": "bank_guarantees", "expiry": expiry},
                )
            )
        return sources

    @staticmethod
    def _previous_position_sources(
        prior_sources: List[SourceEvidence], org_id: str, project_id: str
    ) -> List[SourceEvidence]:
        """Promote the most recent prior letter's summary to fact evidence so
        the draft stays consistent with the position already taken."""
        for source in reversed(prior_sources):
            if source.source_type != "prior_correspondence" or not source.text:
                continue
            return [
                SourceEvidence(
                    source_id=f"position:{source.letter_id or source.source_id}",
                    source_type="prior_correspondence",
                    allowed_use="fact",
                    organization_id=org_id,
                    project_id=project_id,
                    label=f"Previous position — {source.label}",
                    text=source.text,
                    snippet=source.snippet,
                    letter_id=source.letter_id,
                    metadata={**(source.metadata or {}), "previous_position": True},
                )
            ]
        return []

    async def _prior_correspondence_sources(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        current_user: Any,
        org_id: str,
        project_id: str,
        warnings: List[str],
    ) -> Tuple[List[str], List[SourceEvidence]]:
        # The conversation family is assembled from ASSOCIATION - a shared
        # `conversation_id` or a `previous_letter_id` edge - and a reply chain
        # may cross projects and organisations. Authorisation to the anchor
        # letter is therefore not authorisation to its thread, so every member
        # is re-authorised against the ACTOR's canonical row visibility before
        # any of it becomes drafting material. This must happen here, before the
        # source ledger, the prompt, the model call and the DraftRun write.
        #
        # `get_conversation_chain` accepts `current_user` and never reads it;
        # the previous filter compared each entry against the anchor LETTER's
        # organisation/project, which is a workspace-consistency check, not
        # authority - and it skipped the project axis entirely whenever the
        # anchor was organisation-level, because `project_id` is optional on a
        # Letter. That hand-rolled predicate is removed rather than kept beside
        # the real gate: a second thing that looks like authority is how the
        # real one stops being read. The anchor's organisation and project
        # narrow INSIDE the entitlement within the canonical seam, and are
        # never the authority source.
        try:
            chain = await self.conversation_service.get_authorized_conversation_chain(
                str(letter.id), current_user
            )
        except Exception as exc:
            warnings.append(f"Prior correspondence unavailable: {exc}")
            return [], []
        exclude_codes = {normalize_letter_code(str(code)) for code in request.exclude_letter_codes or [] if code}
        prior_ids: List[str] = []
        sources: List[SourceEvidence] = []
        for entry in chain[-6:]:
            if not getattr(entry, "id", None) or str(entry.id) == str(letter.id):
                continue
            if normalize_letter_code(str(getattr(entry, "letter_no", "") or "")) in exclude_codes:
                continue
            prior_ids.append(str(entry.id))
            text = condense_text(
                getattr(entry, "summary", None)
                or getattr(entry, "content", None)
                or getattr(entry, "subject", None),
                1000,
            )
            sources.append(
                SourceEvidence(
                    source_id=f"prior:{entry.id}",
                    source_type="prior_correspondence",
                    allowed_use="history_only",
                    organization_id=org_id,
                    project_id=project_id,
                    label=getattr(entry, "subject", None) or "Prior correspondence",
                    text=text,
                    snippet=condense_text(text, 280),
                    letter_id=str(entry.id),
                    metadata={
                        "letter_no": getattr(entry, "letter_no", None),
                        "date": self._date_label(getattr(entry, "date", None) or getattr(entry, "created_at", None)),
                    },
                )
            )
        return prior_ids, sources

    async def _graph_sources(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        org_id: str,
        project_id: str,
        warnings: List[str],
    ) -> Tuple[List[str], List[SourceEvidence]]:
        codes: List[str] = []
        sources: List[SourceEvidence] = []
        target_code = getattr(letter, "letter_no", None)
        nodes: Sequence[Dict[str, Any]] = []
        if self.graph_service.enabled and target_code:
            try:
                nodes = self.graph_service.get_thread(target_code, depth=6) or []
            except Exception as exc:
                warnings.append(f"Graph thread unavailable: {exc}")
        include_codes = {normalize_letter_code(str(code)) for code in request.include_letter_codes or [] if code}
        exclude_codes = {normalize_letter_code(str(code)) for code in request.exclude_letter_codes or [] if code}
        # G30: a graph node is identity + topology only (G32). Whether its
        # support may be used is decided by the CANONICAL documents behind the
        # code, resolved through the certified foundation - never by the node.
        # Unresolvable provenance fails closed. `build` is async, so this is a
        # real await, not a nested-loop workaround.
        from ...services.publication_policy import graph_codes_denied

        candidate_codes = [
            node.get("normCode") or normalize_letter_code(str(node.get("code") or ""))
            for node in nodes
        ]
        denied_codes = await graph_codes_denied(
            self.db, [code for code in candidate_codes if code] + sorted(include_codes)
        )

        seen: set[str] = set()
        for node in nodes:
            code = node.get("normCode") or normalize_letter_code(str(node.get("code") or ""))
            if not code or code in exclude_codes or code in seen or code in denied_codes:
                continue
            seen.add(code)
            codes.append(code)
            sources.append(
                SourceEvidence(
                    source_id=f"graph:{code}",
                    source_type="graph_thread",
                    allowed_use="history_only",
                    organization_id=org_id,
                    project_id=project_id,
                    # The shared Letter node is identity + topology ONLY (G32):
                    # it is global across documents and tenants, so any `subject`
                    # still on it is legacy contamination owned by whichever
                    # document wrote last - possibly a blocked one, possibly
                    # another tenant's. Allowing the CODE is not permission to
                    # serve the NODE's text, so no node value becomes content and
                    # the raw node never enters metadata.
                    label="Graph-linked letter",
                    snippet=None,
                    metadata={"normCode": code},
                )
            )
        for code in include_codes - seen - exclude_codes - denied_codes:
            codes.append(code)
            sources.append(
                SourceEvidence(
                    source_id=f"graph:{code}",
                    source_type="graph_thread",
                    allowed_use="history_only",
                    organization_id=org_id,
                    project_id=project_id,
                    label="Manually included graph-linked letter",
                    metadata={"normCode": code},
                )
            )
        return codes, sources

    @staticmethod
    def _date_label(value: Any) -> Optional[str]:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).date().isoformat()
        return str(value) if value else None

    @staticmethod
    def _dedupe_sources(sources: List[SourceEvidence]) -> List[SourceEvidence]:
        deduped: List[SourceEvidence] = []
        seen: set[str] = set()
        for source in sources:
            if source.source_id in seen:
                continue
            seen.add(source.source_id)
            deduped.append(source)
        return deduped
