"""Grounded letter summary (Release 1.2).

Stage A (:mod:`.fact_pack`) reads one canonical evidence revision and the
document's structured metadata into facts that each know their page and span.
Stage B (:mod:`.render`, :mod:`.presentation`) composes the short and detailed
summaries from those facts only. :mod:`.service` binds the result to the
evidence revision, persists it and decides whether a stored summary may be
shown.

Nothing here is evidence. Consumers that need facts read the canonical text
(``canonical_evidence_service``); this package never feeds drafting,
chronology, claims or arbitration.
"""
