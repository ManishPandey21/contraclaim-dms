"""ContractScopeResolver — the canonical eligible universe for contract evidence.

One question, one answer: *for this contract, at this query mode, which
instruments govern and which canonical documents may back them?*

The set is built **positively**:

```
eligible_document_ids =
      instruments applicable to (project_id, contract_id) at the query mode
    ∩ canonical Document positively resolvable
    ∩ not publication-blocked
    ∩ projection-current
```

Positive, not subtractive, and that choice is load-bearing. The shared
``blocked_document_ids`` helper deliberately fails OPEN on an id it cannot
resolve — its own docstring calls an absent id "an orphaned point ... a different
problem", which is right for generic search and wrong for legal evidence. This
resolver uses the per-document ``resolve_document_authority``, which fails
CLOSED, so an unresolvable document is excluded because it was never *in* the
set. That is also why the two authority helpers cannot diverge in effect here.

The resolver reads. It writes nothing, and it never persists evidence
readiness — readiness is this computation, run again.

What it does not do: retrieval (tickets 09-12 consume the set), relationship
adaptation (ticket 06), and any lifecycle write. It is a primitive, and it is
safe for it to exist before anything consumes it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status

from ..models.contract_document import (
    ApplicabilityLifecycleKind,
    ApplicabilityQueryMode,
    ApplicableInstrument,
    Browse,
    BrowseResult,
    ContractDocumentType,
    ContractScopeLevel,
    CurrentState,
    Historical,
    ProjectionStatus,
)
from .contract_document_store import (
    APPLICABILITY_COLLECTION,
    APPLICABILITY_EVENTS_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
    LEGAL_EFFECTS_COLLECTION,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AuthorizedContractScope",
    "ContractScopeResolutionError",
    "ContractScopeResolver",
    "ProjectEvidenceUniverse",
    "ResolvedContractScope",
    "authorize_contract_scope",
    "resolve_authorized_project_universe",
]

#: Module-private construction token. Its only job is to make
#: ``AuthorizedContractScope(...)`` impossible from outside this module, so a
#: caller cannot fabricate an authorised scope from raw strings.
_CONSTRUCTION_TOKEN = object()


class ContractScopeResolutionError(Exception):
    """Resolution could not be completed.

    Deliberately an exception rather than an empty result. "Nothing applies" is
    a valid answer a caller may act on; "I could not tell" is not, and returning
    an empty set for both would make them indistinguishable.
    """


@dataclass(frozen=True)
class AuthorizedContractScope:
    """Proof that generic authorisation already happened for this scope.

    The resolver accepts this and nothing else. Raw ``organization_id`` /
    ``project_id`` / ``contract_id`` are inputs to an authorisation decision,
    never the decision itself — so requiring a token makes "call the contract
    resolver directly and skip PolicyService" structurally impossible rather
    than merely discouraged.
    """

    organization_id: str
    project_id: str
    contract_id: str
    actor_id: str
    _token: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _CONSTRUCTION_TOKEN:
            raise TypeError(
                "AuthorizedContractScope cannot be constructed directly. Obtain "
                "one from authorize_contract_scope(), which performs the generic "
                "PolicyService check first."
            )
        for name in ("organization_id", "project_id", "contract_id", "actor_id"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required on an authorised contract scope")

    @classmethod
    def for_tests(
        cls, *, organization_id: str, project_id: str, contract_id: str, actor_id: str
    ) -> "AuthorizedContractScope":
        """Build a token without a PolicyService round trip.

        Test-only, and named so its use in production would be obvious in review.
        """
        return cls(
            organization_id=organization_id,
            project_id=project_id,
            contract_id=contract_id,
            actor_id=actor_id,
            _token=_CONSTRUCTION_TOKEN,
        )


async def authorize_contract_scope(
    policy: Any,
    current_user: Any,
    *,
    permission: str,
    organization_id: str,
    project_id: str,
    contract_id: str,
    audit: bool = True,
) -> AuthorizedContractScope:
    """Run the generic authorisation check, then mint the scope token.

    Denial propagates untouched — a generic refusal keeps its own status rather
    than being translated into an empty catalogue or a not-found, which would
    tell the caller the wrong thing about why they saw nothing.

    The token claims an authorised (organisation, project) *pair*, so the pair is
    proven here as well. ``PolicyService`` proves it for a tenant-bound caller
    (``ScopeService.is_client_scope_allowed``) but returns early for superadmin
    without asking where the project lives; checking it once more, against the
    same database the policy reads, makes a token for org X naming a project of
    Y impossible whoever the caller is. The refusal has the policy's own
    ``scope_denied`` 403 shape and names neither organisation.
    """
    # Proven first: were it after an audited authorize, a refused pair would sit
    # in the audit trail as "allow".
    if not await policy.scope_service.project_belongs_to_organization(
        project_id=str(project_id), organization_id=str(organization_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized: scope_denied"
        )
    await policy.authorize(
        current_user,
        permission,
        resource_type="contract",
        organization_id=organization_id,
        project_id=project_id,
        audit=audit,
    )
    return AuthorizedContractScope(
        organization_id=organization_id,
        project_id=project_id,
        contract_id=contract_id,
        actor_id=str(getattr(current_user, "id", "") or ""),
        _token=_CONSTRUCTION_TOKEN,
    )


@dataclass(frozen=True)
class ResolvedContractScope:
    """The canonical answer for one contract at one query mode."""

    instruments: Tuple[ApplicableInstrument, ...]
    eligible_document_ids: frozenset
    legal_effects: Tuple[Dict[str, Any], ...] = ()


def _parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _is_current(events: Sequence[Dict[str, Any]]) -> bool:
    """Replay by LEGAL effective time.

    An unknown ``effective_at`` sorts first and does not disqualify a current
    answer: absence means unknown, not "never". Recorded time is deliberately
    not consulted — a newest-written event must not win by being newest.
    """
    ordered = sorted(
        events,
        key=lambda e: (_parse_date(e.get("effective_at")) is not None, _parse_date(e.get("effective_at")) or date.min),
    )
    current = False
    for event in ordered:
        kind = event.get("kind")
        if kind == ApplicabilityLifecycleKind.APPLIED.value:
            current = True
        elif kind in (
            ApplicabilityLifecycleKind.WITHDRAWN.value,
            ApplicabilityLifecycleKind.SUPERSEDED.value,
        ):
            current = False
    return current


def _applied_at(events: Sequence[Dict[str, Any]], when: date) -> bool:
    """Was an interval open on ``when``?

    Events with an unknown start are skipped entirely: a dated question needs a
    proven start, and substituting one would manufacture legal evidence.
    """
    # Parsed once and carried alongside the event: the generator already drops
    # the undated ones, but a `key=` lambda re-parsing the same field cannot
    # prove it never returns None. Same order, half the parsing.
    dated = [
        (parsed, event)
        for event, parsed in ((e, _parse_date(e.get("effective_at"))) for e in events)
        if parsed is not None
    ]
    ordered = [event for _, event in sorted(dated, key=lambda pair: pair[0])]
    open_interval = False
    for event in ordered:
        effective = _parse_date(event.get("effective_at"))
        if effective is None or effective > when:
            break
        kind = event.get("kind")
        if kind == ApplicabilityLifecycleKind.APPLIED.value:
            open_interval = True
        elif kind in (
            ApplicabilityLifecycleKind.WITHDRAWN.value,
            ApplicabilityLifecycleKind.SUPERSEDED.value,
        ):
            open_interval = False
    return open_interval


def _projection_is_current(instrument: Dict[str, Any]) -> bool:
    """Strict equality, never ``>=`` and never newest-wins.

    A projection built for revision N stops counting the instant N+1 is
    confirmed — before any worker runs and with no invalidation write.
    """
    if str(instrument.get("projection_status")) != ProjectionStatus.CURRENT.value:
        return False
    return instrument.get("projection_revision") == instrument.get("classification_revision")


class ContractScopeResolver:
    """Resolves one contract's eligible universe. Reads only."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def resolve(
        self, scope: AuthorizedContractScope, mode: ApplicabilityQueryMode
    ) -> ResolvedContractScope:
        if not isinstance(scope, AuthorizedContractScope):
            raise TypeError(
                "resolve() requires an AuthorizedContractScope; raw scope "
                f"identifiers are not authority (got {type(scope).__name__})"
            )
        if isinstance(mode, Browse):
            raise ContractScopeResolutionError(
                "Browse is a catalogue mode and cannot produce contract evidence; "
                "use browse_catalogue() and do not pass its results to a consumer"
            )
        if not isinstance(mode, (CurrentState, Historical)):
            raise ContractScopeResolutionError(f"unsupported query mode: {type(mode).__name__}")

        try:
            aggregates = await self._db[APPLICABILITY_COLLECTION].find(
                {
                    "organization_id": scope.organization_id,
                    "project_id": scope.project_id,
                    "contract_id": scope.contract_id,
                }
            ).to_list(length=None)

            instruments: List[ApplicableInstrument] = []
            eligible: List[str] = []

            for aggregate in aggregates:
                events = await self._db[APPLICABILITY_EVENTS_COLLECTION].find(
                    {"applicability_id": str(aggregate.get("_id"))}
                ).to_list(length=None)

                if isinstance(mode, CurrentState):
                    applies = _is_current(events)
                else:
                    applies = _applied_at(events, mode.event_date)
                if not applies:
                    continue

                record = await self._db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
                    {"_id": aggregate.get("contract_document_id")}
                )
                if record is None:
                    continue
                if not _projection_is_current(record):
                    continue

                document_id = str(record.get("document_id") or "")
                if not await self._document_is_positively_authorised(document_id):
                    continue

                basis = next(
                    (
                        str(e.get("event_id") or e.get("_id"))
                        for e in events
                        if e.get("kind") == ApplicabilityLifecycleKind.APPLIED.value
                    ),
                    "",
                )
                instruments.append(
                    ApplicableInstrument(
                        contract_document_id=str(aggregate.get("contract_document_id")),
                        document_id=document_id,
                        document_version_id=str(record.get("document_version_id") or document_id),
                        organization_id=scope.organization_id,
                        project_id=scope.project_id,
                        contract_id=scope.contract_id,
                        contract_document_type=ContractDocumentType(
                            record.get("contract_document_type")
                            or ContractDocumentType.OTHER_CONTRACTUAL_DOCUMENT.value
                        ),
                        classification_revision=int(record.get("classification_revision") or 1),
                        applicability_event_id=basis,
                    )
                )
                eligible.append(document_id)

            effects = await self._db[LEGAL_EFFECTS_COLLECTION].find(
                {"project_id": scope.project_id, "contract_id": scope.contract_id}
            ).to_list(length=None)

        except ContractScopeResolutionError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately broad, then re-raised
            # Never degrade into an empty result: an empty set is a legitimate
            # answer meaning "nothing applies", and a failure must not be able
            # to impersonate one.
            logger.exception("Contract scope resolution failed")
            raise ContractScopeResolutionError(
                f"contract scope resolution failed for "
                f"({scope.project_id}, {scope.contract_id}): {exc}"
            ) from exc

        return ResolvedContractScope(
            instruments=tuple(instruments),
            eligible_document_ids=frozenset(eligible),
            legal_effects=tuple(effects),
        )

    async def browse_catalogue(
        self, scope: AuthorizedContractScope
    ) -> Tuple[BrowseResult, ...]:
        """Organisation catalogue listing. Returns ``BrowseResult`` only.

        A catalogue entry carries no version, no applicability basis and no
        contract, so it has nothing an evidence consumer needs — passing one
        into evidence cannot half-work.
        """
        if not isinstance(scope, AuthorizedContractScope):
            raise TypeError("browse_catalogue() requires an AuthorizedContractScope")
        try:
            records = await self._db[CONTRACT_DOCUMENTS_COLLECTION].find(
                {"organization_id": scope.organization_id}
            ).to_list(length=None)
            results: List[BrowseResult] = []
            for record in records:
                aggregates = await self._db[APPLICABILITY_COLLECTION].find(
                    {"contract_document_id": str(record.get("_id"))}
                ).to_list(length=None)
                results.append(
                    BrowseResult(
                        contract_document_id=str(record.get("_id")),
                        organization_id=str(record.get("organization_id")),
                        contract_document_type=ContractDocumentType(
                            record.get("contract_document_type")
                            or ContractDocumentType.OTHER_CONTRACTUAL_DOCUMENT.value
                        ),
                        scope_level=ContractScopeLevel(record.get("scope_level")),
                        applicability_count=len(aggregates),
                    )
                )
            return tuple(results)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Contract catalogue browse failed")
            raise ContractScopeResolutionError(f"catalogue browse failed: {exc}") from exc

    async def _document_is_positively_authorised(self, document_id: str) -> bool:
        """Positive resolution via the FAIL-CLOSED per-document helper.

        ``blocked_document_ids`` is not used here: it fails open on an id it
        cannot resolve, so a subtractive gate would admit an orphaned candidate.
        """
        if not document_id:
            return False
        from .publication_policy import resolve_document_authority

        decision = await resolve_document_authority(self._db, document_id)
        return bool(getattr(decision, "consumable", False))


# --------------------------------------------------------------------------- #
# Project-wide evidence universe — for consumers that have no contract identity
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProjectEvidenceUniverse:
    """The canonical eligible universe for a PROJECT, not for one contract.

    Some evidence consumers — letter drafting v2 is the first — are anchored on
    a Letter and carry no contract identifier at all. The frozen evidence model
    is per ``(project_id, contract_id)`` and that is not weakened here: this
    type is the UNION of per-contract resolutions over the contracts that
    actually have applicability aggregates in the project. Every document in it
    passed the full per-contract test (applicability at the query mode,
    positive canonical Document resolution, publication authority, projection
    currency); none of it was derived from a clause row, a vector payload, a
    graph property or a document's own ``project_id``.

    The residual imprecision is recorded rather than hidden: a consumer with no
    contract identity cannot distinguish two contracts inside one project, so a
    clause governing sibling contract ``OTHER`` is admitted to a draft about
    contract ``MAIN``. Narrowing that needs contract identity on the consumer's
    request — an owner decision, not something this module may invent.
    """

    organization_id: str
    project_id: str
    contract_ids: Tuple[str, ...]
    eligible_document_ids: frozenset
    instruments: Tuple[ApplicableInstrument, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.eligible_document_ids


def _actor_is_entitled(current_user: Any, organization_id: str, project_id: str) -> bool:
    """Canonical entitlement, resolved by role tier and never by role NAME.

    Denial is a legitimate ANSWER — this actor's universe is empty — so it
    returns False rather than raising. A resolution FAILURE is a different
    thing and still raises, because an empty set must never be able to
    impersonate a broken lookup.
    """
    from ..core.security import authorize_scope

    try:
        authorize_scope(current_user, organization_id, project_id)
    except Exception:
        return False
    return True


async def resolve_authorized_project_universe(
    db: Any,
    current_user: Any,
    *,
    organization_id: str,
    project_id: str,
    mode: ApplicabilityQueryMode,
) -> ProjectEvidenceUniverse:
    """One canonical eligible universe for one actor, project and query mode.

    The construction token stays private to this module: this function mints a
    scope only AFTER running the canonical entitlement check itself, so it is
    not a hole in ``AuthorizedContractScope``'s guarantee — it is a second
    authorised door with its own lock.

    Raises ``ContractScopeResolutionError`` when the universe cannot be
    computed. It never returns a partial set, and it has no legacy path to fall
    back to: falling back to generic contract search is exactly the door the
    Contract Master model closes.
    """
    organization_id = str(organization_id or "")
    project_id = str(project_id or "")
    actor_id = str(getattr(current_user, "id", "") or "")

    empty = ProjectEvidenceUniverse(
        organization_id=organization_id,
        project_id=project_id,
        contract_ids=(),
        eligible_document_ids=frozenset(),
    )

    # No workspace and no principal are answers, not failures: there is nothing
    # this actor may see, which is a valid empty universe.
    if not organization_id or not project_id or not actor_id:
        return empty
    if not _actor_is_entitled(current_user, organization_id, project_id):
        return empty

    try:
        aggregates = await db[APPLICABILITY_COLLECTION].find(
            {"organization_id": organization_id, "project_id": project_id}
        ).to_list(length=None)
    except Exception as exc:  # noqa: BLE001 - re-raised, never degraded
        logger.exception("Contract applicability enumeration failed")
        raise ContractScopeResolutionError(
            f"contract applicability enumeration failed for "
            f"({organization_id}, {project_id}): {exc}"
        ) from exc

    contract_ids = sorted(
        {
            str(aggregate.get("contract_id"))
            for aggregate in aggregates
            if aggregate.get("contract_id")
        }
    )
    if not contract_ids:
        return empty

    resolver = ContractScopeResolver(db)
    eligible: set = set()
    instruments: List[ApplicableInstrument] = []
    for contract_id in contract_ids:
        scope = AuthorizedContractScope(
            organization_id=organization_id,
            project_id=project_id,
            contract_id=contract_id,
            actor_id=actor_id,
            _token=_CONSTRUCTION_TOKEN,
        )
        resolved = await resolver.resolve(scope, mode)
        eligible.update(str(item) for item in resolved.eligible_document_ids)
        instruments.extend(resolved.instruments)

    return ProjectEvidenceUniverse(
        organization_id=organization_id,
        project_id=project_id,
        contract_ids=tuple(contract_ids),
        eligible_document_ids=frozenset(eligible),
        instruments=tuple(instruments),
    )
