"""Deterministic factual-grounding check for a composed letter.

It runs after generation and does not depend on the drafting prompt. Each
sentence of the draft is classified; a sentence that states a fact must be
backed by an independent source passage that carries the same specifics
(numbers, dates, references) and most of the same content words. The sender's
own letter backs only statements attributed to the sender, never a statement
of fact. A citation alone does not make a sentence supported: the cited
clause's text must overlap the sentence.

This is a lexical check. It cannot judge meaning, so it is tuned to refuse
rather than accept: a sentence it cannot match is ``unsupported``, and a
reviewer resolves it (replace the wording, or confirm it with a recorded
basis). It never uses a model score as evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

from ...models.letter_drafting import GroundingStatement, SourceEvidence, ValidationFinding, ValidationReport

EVIDENCE_TYPES = {"contract_clause", "context_document", "prior_correspondence", "graph_thread"}
SUPPORTED_AT = 0.6
PARTIAL_AT = 0.35
CLAUSE_OVERLAP_AT = 0.2

_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])\s+(?=[A-Z0-9\[(\"'])|\n{1,}")
_PLACEHOLDER = re.compile(r"\[(?:CONFIRM:|TO BE INSERTED BY USER:|POSITION CONFLICT:)[^\]]*\]", re.I)
_FRAMING = re.compile(
    r"^\s*(to|subject|ref|date|dear|yours|for and on behalf|sincerely|cc|encl|attachments?)\b", re.I
)
_CLAUSE = re.compile(r"\b(?:sub-?clause|clause|article|section)\s+([0-9][0-9A-Za-z.\-()]*)", re.I)
_PARTY = r"(?:the\s+)?(?:contractor|employer|engineer|sub-?contractor|consultant|client|you|your letter|your notice|the sender)"
_ATTRIBUTION = re.compile(
    _PARTY + r"(?!'s)(?!s\b)\b(?:\s+(?!(?:is|was|are|were)\b)[\w./,-]+){0,6}?\s+(?:states?|stated|claims?|claimed|contends?|contended|asserts?|asserted|alleges?|alleged|"
    r"says|said|submits?|submitted that|maintains?|argues?|requests?|requested|refers?|referred|seeks?|sought|"
    r"demands?|demanded|notif(?:y|ies|ied)|has given notice|gives notice)\b",
    re.I,
)
_REQUEST = re.compile(
    r"\b(is|are) (?:hereby )?(?:requested|required|called upon|asked|invited) to|\bplease\b|\bkindly\b|"
    r"\b(?:shall|must|should) (?:provide|submit|furnish|produce|substantiate|demonstrate)\b|"
    r"\brequest(?:s|ed)? (?:that|you|the contractor|the employer|the engineer)\b",
    re.I,
)
_CORRESPONDENCE = re.compile(
    r"\b(?:your|their|the (?:contractor|employer|engineer)'?s?) (?:letter|notice|reference|communication|email|submission|claim|request|demand)\b",
    re.I,
)
_ALLEGED = re.compile(r"\b(alleged(?:ly)?|purported(?:ly)?|claimed|so-called)\b", re.I)
_ENGINEER_FINDING = re.compile(r"\bengineer\b[^.]{0,40}\b(determin\w*|found|certif\w*|decided|assessed)\b", re.I)
_POSITION = re.compile(
    r"\b(does not accept|do not accept|not accepted|cannot accept|rejects?|is rejected|are rejected|accepts?|is accepted|"
    r"disputes?|is disputed|denies|denied|is not entitled|are not entitled|no entitlement|is entitled|are entitled|"
    r"entitlement is|in the (?:employer|contractor|engineer)'?s? view|it is the \w+'?s? position|position is|"
    r"maintains? (?:its|the) position|reserves?|without prejudice|is requested to|are requested to|requests that|"
    r"request(?:s|ed)? (?:you|the contractor|the employer)|please|shall provide|must provide|should provide|"
    r"is required to|are required to|kindly|we refer to|refers? to your|reply to each point|note the positions)\b",
    re.I,
)
_EVIDENCE_CLAIM = re.compile(
    r"\b(we have (?:reviewed|examined|investigated|inspected|assessed|checked|verified)|"
    r"(?:our|the) (?:review|evaluation|investigation|inspection|assessment|examination|analysis)\b|"
    r"records?\s+(?:show|shows|indicate|indicates|confirm|confirms|do not|does not|demonstrate)|"
    r"(?:site|contemporaneous|project|daily) (?:records|diaries|logs) (?:show|shows|indicate|confirm|record|do not|does not|demonstrate)|"
    r"(?:has|have|was|were) not been (?:met|satisfied|complied|fulfilled|provided|submitted|received)|"
    r"(?:has|have|was|were) (?:been )?(?:met|satisfied|complied with|fulfilled|issued|instructed|approved|"
    r"submitted|received|notified|certified|determined)|"
    r"(?:is|was|were) (?:in accordance with|compliant|non-compliant|defective|not defective)|"
    r"do(?:es)? not substantiate|(?:is|are) not substantiated|indicates? that|shows? that|establish(?:es|ed)? that|"
    r"it (?:is|was) (?:found|established|confirmed|evident))",
    re.I,
)
_SPECIFIC = re.compile(
    r"(?:rs\.?|inr|₹)\s?[0-9][0-9,]*(?:\.[0-9]+)?|\b[0-9]{1,2}[-/.][0-9]{1,2}[-/.][0-9]{2,4}\b|\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b|"
    r"\b[0-9]+(?:[.,][0-9]+)*\s?(?:days?|weeks?|months?|%|per cent|lakh|crore|m|km|mm|cum|sqm|nos?)\b|"
    r"\b(?:no\.?|ref\.?|letter)\s?[A-Z0-9][A-Z0-9/\-_.]{2,}\b|\b[A-Z]{2,}[/\-][A-Z0-9/\-_.]{2,}\b|"
    r"\b[0-9]{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+[0-9]{4}\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+[0-9]{1,2},?\s+[0-9]{4}\b",
    re.I,
)
_WORD = re.compile(r"[a-z0-9]+")
_MODAL = re.compile(r"\b(will|shall|should|may|might|would|could|must|can|cannot|to be)\b", re.I)
_PROCEDURAL = re.compile(
    r"\b(acknowledge|acknowledges|acknowledged receipt|thank|thanks|enclose|enclosed|attach|attached|"
    r"repl(?:y|ies) to each point|respond(?:s)? to each point|set out (?:below|above)|as follows)\b",
    re.I,
)
_DECLARATIVE = re.compile(
    r"\b(?:was|were|has been|have been|had been|is|are)\s+(?:\w+\s+)?\w+(?:ed|en|wn)\b|\b\w+ed\b|"
    r"\b(?:shows?|records?|confirms?|indicates?|caused|found|instructed|issued|approved|gave|given|made|paid|reduced|increased)\b",
    re.I,
)
_PROVISION = re.compile(
    r"\b(requires?|provides?|entitles?|obliges?|stipulates?|states? that|under (?:sub-?)?clause|in accordance with (?:sub-?)?clause)\b",
    re.I,
)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ABBREVIATIONS = re.compile(r"\b(No|Nos|Rs|Ref|Cl|Sr|Dt|Encl|viz|etc|Mr|Ms|Dr|St|Km|Ch)\.(?=\s)", re.I)
_STOP = set(
    "a an the and or of to in on at by for with from as is are was were be been being this that these those it its "
    "we our you your they their he she his her which who whom whose there here has have had do does did not no "
    "shall will would should may might can could must any all each such other than then into upon under within "
    "also only very so if but about above below over between per further therefore hereby herein thereof".split()
)


def _content(text: str) -> set:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def _norm_specific(token: str) -> str:
    token = token.lower().replace(",", "").replace("₹", "rs").replace("inr", "rs").replace(" ", "")
    # "letter NCW/118", "ref. NCW/118" and "NCW/118" are the same reference.
    token = re.sub(r"^(?:letter|ref\.?|no\.?)(?=[a-z0-9])", "", token)
    named = re.fullmatch(r"([0-9]{1,2})(?:st|nd|rd|th)?([a-z]{3})[a-z]*\.?,?([0-9]{4})", token) or re.fullmatch(
        r"([a-z]{3})[a-z]*\.?([0-9]{1,2}),?([0-9]{4})", token
    )
    if named:
        a, b, y = named.groups()
        d, mon = (a, b) if a.isdigit() else (b, a)
        if mon in _MONTHS:
            return f"{y}-{_MONTHS[mon]:02d}-{int(d):02d}"
    date = re.fullmatch(r"([0-9]{1,2})[-/.]([0-9]{1,2})[-/.]([0-9]{2,4})", token)
    if date:
        d, m, y = date.groups()
        y = ("20" + y) if len(y) == 2 else y
        return f"{y}-{int(m):02d}-{int(d):02d}"
    token = re.sub(r"^rs\.?", "rs", token)
    token = re.sub(r"(days?|weeks?|months?)$", lambda m: m.group(1).rstrip("s"), token)
    return token.rstrip(".")


def _specifics(text: str) -> set:
    # A specific carries a digit: "Rs. 4,50,000", "31 days", "NCW/118". Not "notice".
    return {_norm_specific(m.group(0)) for m in _SPECIFIC.finditer(text) if any(ch.isdigit() for ch in m.group(0))}


def _number(token: str) -> Optional[str]:
    match = re.search(r"[0-9][0-9.]*", token)
    return match.group(0) if match else None


@dataclass
class _Passage:
    text: str
    words: set
    specifics: set
    source: Optional[SourceEvidence]
    kind: str  # "evidence" | "user" | "sender"


@dataclass
class Confirmation:
    text: str
    basis: str
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None


@dataclass
class GroundingInputs:
    sources: Sequence[SourceEvidence]
    incoming_document_id: Optional[str] = None
    sender_texts: Sequence[str] = ()
    user_inputs: Sequence[str] = ()
    confirmations: Sequence[Confirmation] = field(default_factory=list)


def split_sentences(text: str) -> List[str]:
    # "IPC No. 7" and "Rs. 4,50,000" do not end a sentence: hide those dots while splitting.
    protected = _ABBREVIATIONS.sub(lambda m: m.group(0)[:-1] + "․", text or "")
    parts = (p.strip().replace("․", ".") for p in _SENTENCE_SPLIT.split(protected))
    return [p for p in parts if p and len(_WORD.findall(p)) >= 2]


def _passages(texts: Iterable[str], source: Optional[SourceEvidence], kind: str) -> List[_Passage]:
    out = []
    for text in texts:
        for sentence in split_sentences(text):
            out.append(_Passage(sentence, _content(sentence), _specifics(sentence), source, kind))
    return out


def _corpus(inputs: GroundingInputs) -> List[_Passage]:
    passages: List[_Passage] = []
    for source in inputs.sources:
        text = source.text or source.snippet or ""
        if not text:
            continue
        is_incoming = bool(inputs.incoming_document_id) and str(source.document_id or "") == str(
            inputs.incoming_document_id
        )
        kind = "sender" if is_incoming else ("evidence" if source.source_type in EVIDENCE_TYPES else None)
        if kind:
            passages.extend(_passages([text], source, kind))
    passages.extend(_passages(inputs.user_inputs, None, "user"))
    passages.extend(_passages(inputs.sender_texts, None, "sender"))
    return passages


def _best(sentence_words: set, sentence_specifics: set, passages: Sequence[_Passage]):
    best, best_score = None, 0.0
    for passage in passages:
        if not sentence_words:
            break
        score = len(sentence_words & passage.words) / len(sentence_words)
        if sentence_specifics and not sentence_specifics <= passage.specifics:
            score = min(score, PARTIAL_AT - 0.01) if score >= SUPPORTED_AT else score * 0.9
        if score > best_score:
            best, best_score = passage, score
    return best, best_score


def _contradiction(sentence_specifics: set, passage: _Passage) -> Optional[str]:
    """Same unit, different value, in a passage about the same thing."""
    for mine in sentence_specifics:
        unit = re.sub(r"[0-9.\-]", "", mine)
        for theirs in passage.specifics:
            if unit and unit == re.sub(r"[0-9.\-]", "", theirs) and _number(mine) != _number(theirs):
                return f"{passage.text} ({theirs} where the draft says {mine})"
    return None


def _statement(text: str, **kwargs) -> GroundingStatement:
    return GroundingStatement(text=text, **kwargs)


def _from_passage(passage: Optional[_Passage]) -> dict:
    if passage is None:
        return {}
    source = passage.source
    return {
        "passage": passage.text[:600],
        "source_id": source.source_id if source else None,
        "source_label": (source.label if source else ("User input" if passage.kind == "user" else "Incoming letter")),
        "document_id": source.document_id if source else None,
        "location": ", ".join(f"p. {p}" for p in (source.page_numbers or [])) if source else None,
    }


def analyse(draft: str, inputs: GroundingInputs) -> List[GroundingStatement]:
    corpus = _corpus(inputs)
    evidence = [p for p in corpus if p.kind == "evidence"]
    user = [p for p in corpus if p.kind == "user"]
    sender = [p for p in corpus if p.kind == "sender"]
    clause_texts = {
        (s.clause_number or "").lower().strip(" .;,"): s for s in inputs.sources if s.source_type == "contract_clause"
    }
    confirmed = [(c, _content(c.text)) for c in inputs.confirmations if c.text.strip()]
    statements: List[GroundingStatement] = []

    for sentence in split_sentences(draft):
        if _FRAMING.match(sentence):
            continue
        placeholder = bool(_PLACEHOLDER.search(sentence))
        # What a placeholder asks the reviewer to check is not a claim.
        claim_text = _PLACEHOLDER.sub(" ", sentence)
        clauses = [c.lower().strip(" .;,") for c in _CLAUSE.findall(claim_text)]
        # Citation words ("Sub-Clause 20.2") are not content to be matched.
        words, specifics = _content(_CLAUSE.sub(" ", claim_text)), _specifics(_CLAUSE.sub(" ", claim_text))
        evidence_claim = bool(_EVIDENCE_CLAIM.search(claim_text))
        attributed = bool(_ATTRIBUTION.search(claim_text) or _ALLEGED.search(claim_text))
        request = bool(_REQUEST.search(claim_text))
        position = bool(_POSITION.search(claim_text))

        for confirmation, cwords in confirmed:
            if words and len(words & cwords) / len(words) >= SUPPORTED_AT:
                statements.append(_statement(
                    sentence, classification="verified_fact", status="user_confirmed", material=True,
                    confirmation_basis=confirmation.basis, confirmed_by=confirmation.confirmed_by,
                    confirmed_at=confirmation.confirmed_at, source_label="Confirmed by reviewer",
                ))
                break
        else:
            if placeholder and (not claim_text.strip(" .;,") or not (evidence_claim or specifics) or len(words) <= 2):
                statements.append(_statement(
                    sentence, classification="requires_confirmation", status="unsupported", material=True,
                    requires_confirmation=True,
                ))
                continue
            if request and not specifics:
                statements.append(_statement(sentence, classification="proposed_argument", status="not_applicable", material=False))
                continue
            invented_clauses = [
                c for c in clauses
                if c not in clause_texts and not any(re.search(r"(?<![0-9.])" + re.escape(c) + r"(?![0-9])", p.text) for p in sender)
            ]
            if invented_clauses:
                # Cited by no contract source and not by the sender either.
                statements.append(_statement(
                    sentence, classification="contractual_provision", status="unsupported", material=True,
                    contradiction=f"Not in the contract sources or the correspondence: Clause {', '.join(invented_clauses)}",
                    requires_confirmation=True,
                ))
                continue
            if _ENGINEER_FINDING.search(claim_text) and not attributed:
                classification = "engineer_finding"
            elif (attributed or _CORRESPONDENCE.search(claim_text)) and not evidence_claim:
                # What a party said, or which letter is being answered. Not a fact
                # in itself - but the numbers, dates and references it repeats
                # must be the ones that party actually wrote: a reference or an
                # amount found nowhere is a fabricated or misstated one.
                party = "contractor" if re.search(r"contractor|you|your", sentence, re.I) else "employer"
                known = set().union(*(p.specifics for p in sender + evidence + user)) if corpus else set()
                misstated = sorted(specifics - known)
                passage, score = _best(words, specifics, sender + evidence)
                statements.append(_statement(
                    sentence,
                    classification=f"{party}_assertion",
                    status="unsupported" if misstated else ("supported" if score >= PARTIAL_AT or specifics else "not_applicable"),
                    material=bool(misstated),
                    contradiction=f"Not found in the correspondence or sources: {', '.join(misstated)}" if misstated else None,
                    requires_confirmation=bool(misstated),
                    **_from_passage(passage if (score >= PARTIAL_AT and not misstated) else None),
                ))
                continue
            elif clauses and not evidence_claim and not specifics:
                missing = [c for c in clauses if c not in clause_texts]
                cited = [clause_texts[c] for c in clauses if c in clause_texts]
                overlap = max(
                    (len(words & _content(s.text or "")) / max(len(words), 1) for s in cited), default=0.0
                )
                if missing:
                    status = "unsupported"
                elif position or overlap >= CLAUSE_OVERLAP_AT:
                    status = "supported" if overlap >= CLAUSE_OVERLAP_AT else "partially_supported"
                else:
                    status = "partially_supported"
                source = cited[0] if cited else None
                statements.append(_statement(
                    sentence,
                    classification="contractual_interpretation" if position else "contractual_provision",
                    status=status, material=bool(missing),
                    source_id=source.source_id if source else None, source_label=source.label if source else None,
                    passage=(source.text or "")[:600] if source else None, document_id=source.document_id if source else None,
                ))
                continue
            elif position and _MODAL.search(claim_text) and not specifics:
                # A position about what will or may happen ("only the delay the
                # records support will be considered") asserts no past fact.
                statements.append(_statement(sentence, classification="proposed_argument", status="not_applicable", material=False))
                continue
            elif position and not evidence_claim and not specifics:
                statements.append(_statement(sentence, classification="proposed_argument", status="not_applicable", material=False))
                continue
            elif evidence_claim or specifics:
                classification = "verified_fact"
            elif _DECLARATIVE.search(claim_text) and not _MODAL.search(claim_text) and not _PROCEDURAL.search(claim_text):
                # A plain statement that something happened ("the Engineer instructed
                # ...", "the delay was caused by ...") is a factual claim.
                classification = "verified_fact"
            else:
                statements.append(_statement(sentence, classification="non_factual", status="not_applicable", material=False))
                continue

            # Contract wording states obligations, not events: it backs a sentence
            # about what a clause provides, never one about what happened.
            provision = bool(clauses) or bool(_PROVISION.search(claim_text))
            pool = [p for p in evidence if provision or not (p.source and p.source.source_type == "contract_clause")]
            passage, score = _best(words, specifics, pool + user)
            contradiction = _contradiction(specifics, passage) if passage and specifics and score >= PARTIAL_AT * 0.9 else None
            if contradiction:
                status = "contradicted"
            elif score >= SUPPORTED_AT:
                status = "user_confirmed" if passage and passage.kind == "user" else "supported"
            elif score >= PARTIAL_AT and not (specifics and not (passage and specifics <= passage.specifics)):
                status = "partially_supported"
            else:
                status = "unsupported"
                only_sender, s_score = _best(words, specifics, sender)
                if s_score >= SUPPORTED_AT:
                    classification = "unverified_assertion"  # the sender's claim restated as fact
            statements.append(_statement(
                sentence, classification=classification, status=status, material=True,
                contradiction=contradiction, requires_confirmation=status in {"unsupported", "partially_supported"},
                **_from_passage(passage if status != "unsupported" else None),
            ))
    return statements


def grounding_findings(statements: Sequence[GroundingStatement]) -> ValidationReport:
    """Unsupported or contradicted material facts block; partial support warns.

    Placeholders are reported by DraftValidator (placeholders_present) and held
    at approval, so they are not repeated here.
    """
    findings: List[ValidationFinding] = []
    for s in statements:
        if not s.material or s.classification == "requires_confirmation":
            continue
        if s.status == "contradicted":
            findings.append(ValidationFinding(
                level="error", code="contradicted_factual_statement",
                message="A factual statement in the draft is contradicted by a source.",
                evidence=f"{s.text[:300]} | source: {(s.contradiction or '')[:300]}",
            ))
        elif s.status == "unsupported":
            findings.append(ValidationFinding(
                level="error", code="unsupported_factual_statement",
                message="A factual statement in the draft is not supported by any source or user input.",
                evidence=s.text[:400],
            ))
        elif s.status == "partially_supported":
            findings.append(ValidationFinding(
                level="warning", code="partially_supported_statement",
                message="A factual statement is only partly matched by its source; check it.",
                evidence=s.text[:400],
            ))
    return ValidationReport(blocking=any(f.level == "error" for f in findings), findings=findings)


__all__ = ["Confirmation", "GroundingInputs", "analyse", "grounding_findings", "split_sentences"]
