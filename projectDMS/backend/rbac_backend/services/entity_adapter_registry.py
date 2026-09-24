"""Server-owned target adapters for canonical document relationships."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, FrozenSet, Optional
from urllib.parse import quote

from ..core.permissions import Permissions
from .publication_policy import document_id_candidates


CLAIM_DOCUMENT_ROLES: FrozenSet[str] = frozenset(
    {
        "notice",
        "claim_submission",
        "supporting_document",
        "engineer_response",
        "employer_response",
        "determination",
        "correspondence",
    }
)

IPC_DOCUMENT_ROLES: FrozenSet[str] = frozenset(
    {
        "ipc_submission",
        "certified_ipc",
        "invoice",
        "payment_certificate",
        "supporting_document",
        "payment_correspondence",
    }
)

BANK_GUARANTEE_EVENT_ROLES: Dict[str, FrozenSet[str]] = {
    "original": frozenset({"original_bg", "supporting_document", "correspondence"}),
    "submission": frozenset({"submission", "original_bg", "supporting_document", "correspondence"}),
    "extension": frozenset({"extension", "supporting_document", "correspondence"}),
    "release": frozenset({"release", "supporting_document", "correspondence"}),
}

KEY_DATE_ACHIEVEMENT_ROLES: FrozenSet[str] = frozenset(
    {
        "contractor_notification",
        "engineer_acknowledgement",
        "completion_certificate",
        "inspection_record",
        "supporting_document",
        "correspondence",
    }
)

#: Contract Master v1 permits exactly one generic relationship role.
#:
#: Canonical instrument identity is ContractDocument.document_id -- a direct
#: field on the domain record. Expressing it a second time as a link row would
#: be a second representation of the same fact to keep in sync, so there is no
#: 'primary_document' role here and must not be one later.
CONTRACT_DOCUMENT_ROLES: FrozenSet[str] = frozenset({"supporting_document"})


EOT_SUBMISSION_ROLES: FrozenSet[str] = frozenset(
    {
        "eot_submission",
        "eot_supporting_document",
        "supporting_document",
        "correspondence",
    }
)

EOT_DETERMINATION_ROLES: FrozenSet[str] = frozenset(
    {
        "eot_determination",
        "engineer_determination",
        "supporting_document",
        "correspondence",
    }
)

#: Evidence roles for a Hindrance & Constraint Register entry (`delay_events`).
HINDRANCE_DOCUMENT_ROLES: FrozenSet[str] = frozenset(
    {
        "notice",
        "correspondence",
        "instruction",
        "site_record",
        "photograph",
        "programme_record",
        "supporting_document",
    }
)

INSURANCE_DOCUMENT_ROLES: FrozenSet[str] = frozenset(
    {
        "policy",
        "certificate",
        "correspondence",
        "supporting_document",
    }
)


#: Variation evidence, derived from the current Variation model and workflow
#: (draft -> submitted -> under_review -> recommended -> approved/rejected):
#:
#: * ``variation_submission`` - the submission backing ``submitted_amount``;
#: * ``variation_approval`` - the approval backing ``approved_amount`` /
#:   ``approval_date``;
#: * ``correspondence`` - any letter about the variation (``letter_reference``);
#: * ``supporting_document`` - the generic fallback.
#:
#: The model has no instruction or quotation concept, so neither role exists.
VARIATION_DOCUMENT_ROLES: FrozenSet[str] = frozenset(
    {
        "variation_submission",
        "variation_approval",
        "correspondence",
        "supporting_document",
    }
)


#: Programme Milestone evidence (CL-3B), derived from the ProgrammeMilestone
#: model (``planned_date`` / ``forecast_date`` / ``actual_date`` / ``status``):
#:
#: * ``programme_record`` - the programme submission or update behind the
#:   planned/forecast dates (the role a Hindrance already uses for it);
#: * ``progress_evidence`` - what backs ``actual_date`` or a progress ``status``
#:   (achieved / in progress / delayed): site reports, inspection records;
#: * ``correspondence`` - any letter about the milestone;
#: * ``supporting_document`` - the generic fallback, and the meaning of the
#:   legacy ``linked_document_ids`` (G31's "supporting documents").
#:
#: The model has no approval or instruction concept, so neither role exists.
PROGRAMME_MILESTONE_DOCUMENT_ROLES: FrozenSet[str] = frozenset(
    {
        "programme_record",
        "progress_evidence",
        "correspondence",
        "supporting_document",
    }
)

#: Chronology event evidence a user may LINK (CL-3B). ``related_document_ids``
#: were generic "related documents", so the linkable roles are the generic pair.
#:
#: ``source_document`` is deliberately NOT linkable. An event's
#: ``source_document_id`` is its extraction provenance: exactly one Document,
#: the one its title/description/spans were lifted from and whose publication
#: authority governs whether that text may be served (``publication_policy``).
#: Expressing it a second time as a link row would be a second representation
#: of the same fact to keep in sync - the Contract Master precedent above - and
#: would let one source become many. It is surfaced read-only, as the
#: ``source_document`` role of the legacy read-through, and never written.
CHRONOLOGY_EVENT_DOCUMENT_ROLES: FrozenSet[str] = frozenset(
    {
        "correspondence",
        "supporting_document",
    }
)

#: Read-only role under which a chronology event's ``source_document_id`` is shown.
CHRONOLOGY_SOURCE_DOCUMENT_ROLE = "source_document"


#: Roles that assert the linked Document *is correspondence*.
#:
#: Other roles (supporting_document, notice, determination, ...) describe the
#: Document's purpose for the target and may point at any Document type; these
#: two make a claim about the Document itself, so the server checks it.
CORRESPONDENCE_ROLES: FrozenSet[str] = frozenset({"correspondence", "payment_correspondence"})

#: The correspondence values of the Document ``uploadType`` taxonomy
#: (``incoming | outgoing | contract``, case-insensitive per the Document model).
CORRESPONDENCE_UPLOAD_TYPES: FrozenSet[str] = frozenset({"incoming", "outgoing"})


def is_correspondence_document(document: Optional[Dict[str, Any]]) -> bool:
    """Is this Document incoming or outgoing correspondence?

    Fails closed: a missing or unrecognised ``uploadType`` is not correspondence.
    The legacy ``upload_type`` spelling is read only when ``uploadType`` is absent.
    """
    if not document:
        return False
    raw = document.get("uploadType")
    if raw is None:
        raw = document.get("upload_type")
    return str(raw or "").strip().lower() in CORRESPONDENCE_UPLOAD_TYPES


#: A trailing count ("Extension 2", "2") is label text no row field holds.
_TRAILING_COUNT = re.compile(r"(^|\s)\d+$")


def _label_tail_search(needle: str) -> bool:
    """Does ``needle`` reach past the parent part of a composed label?

    Composed labels read "<parent> · <event> <n>"; a search spanning the
    separator or ending in the count is matched only by the rendered label, so
    such a search must not be narrowed by parent fields (it would drop matches).
    """
    return "·" in needle or bool(_TRAILING_COUNT.search(needle))


async def _matching_parent_ids(collection: Any, query: Dict[str, Any]) -> list[Any]:
    """Ids of the parent rows matching ``query``, in the forms a child may store.

    Children keep their parent's id as a string; the parent's own ``_id`` may be
    an ObjectId. Both forms are returned so ``$in`` matches either spelling.
    """
    ids: list[Any] = []
    async for row in collection.find(query, {"_id": 1}):
        raw = row.get("_id")
        if raw is None:
            continue
        ids.append(raw)
        if not isinstance(raw, str):
            ids.append(str(raw))
    return ids


@dataclass(frozen=True)
class EntityContext:
    target_type: str
    target_id: str
    entity: Dict[str, Any]
    organization_id: str
    project_id: str
    view_permission: str
    manage_permission: str
    delete_permission: str
    allowed_roles: FrozenSet[str]
    label: str
    route: str
    frozen: bool
    parent_type: Optional[str] = None
    parent_id: Optional[str] = None
    freeze_permission: Optional[str] = None


class EntityAdapter:
    target_type: str
    #: The register collection holding this target's rows.
    target_collection: str = ""
    #: Row fields the display label is built from, for a server-side search
    #: prefilter. Empty when the label comes from a parent row.
    target_label_fields: tuple[str, ...] = ()
    #: Whether the selected navbar project (``X-Proj-Id``, ``core/tenant_context``)
    #: bounds this target on the shared relationship routes: a target outside the
    #: selection is 403 ``context_forbidden``, a record-level request with nothing
    #: selected is 400 ``selection_required``, and the Document reverse lookup
    #: hides the target's rows outside the selection. Only registers that enforce
    #: the selection on their own routes opt in (CL-3A: Variation, Hindrance; CL-3B:
    #: Programme Milestone, Chronology event; CL-4A: every core register and the
    #: Contract Document); a target that does not keeps its selection-blind behaviour.
    active_scope_enforced: bool = False
    #: Offered by "Link to Record" from the Document side. Only targets whose
    #: adapter, deep link and register UI are verified end to end opt in; the
    #: legacy ``bank_guarantee`` parent (no writable roles) and
    #: ``contract_document`` (linked from the Contract Master side) do not.
    link_to_record: bool = False
    #: Every permission that can be this type's ``manage_permission``. Holding
    #: none of them in a scope means no record of the type can take a link
    #: there - answerable without reading a single record.
    link_manage_permissions: tuple[str, ...] = ()
    #: Fixed text every label of this type ends with (" · Achievement"): a
    #: search for part of it matches every row, so no row prefilter applies.
    target_label_suffix: str = ""
    #: Does a legacy reference to a Document (read through by
    #: ``legacy_targets_for_document``) block that Document's deletion? True for
    #: the registers that already blocked it; the CL-3B registers never did, and a
    #: chronology event's source is provenance the publication policy already
    #: handles for a deleted Document, so they keep deletion as it was.
    legacy_blocks_document_deletion: bool = True
    legacy_relationship_role = "manual_review"
    supports_freeze = True
    freeze_requires_lifecycle_orchestration = False

    async def link_target_search(
        self,
        db: Any,
        needle: str,
        *,
        organization_id: str,
        project_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Database predicate narrowing Link-to-Record candidates to ``needle``.

        ``None`` means no narrowing: every in-scope row is a candidate. The label
        check after ``load`` stays the authority; this must keep every matching
        row and should drop the rest, so a large register is searchable past
        the scan budget instead of only within its first rows.
        """
        if not needle or not self.target_label_fields:
            return None
        if needle in self.target_label_suffix.lower():
            return None
        pattern = {"$regex": re.escape(needle), "$options": "i"}
        return {"$or": [{field: pattern} for field in self.target_label_fields]}

    async def link_target_scope(
        self,
        db: Any,
        *,
        organization_id: str,
        project_id: str,
    ) -> Dict[str, Any]:
        """Row filter for the Link-to-Record candidates of one Document scope."""
        return {"organization_id": organization_id, "project_id": project_id}

    def legacy_document_ids(self, entity: Dict[str, Any]) -> list[str]:
        """Document ids a target row carries outside ``entity_document_links``."""
        return [str(item) for item in (entity.get("linked_document_ids") or []) if str(item or "")]

    def legacy_role_for(self, entity: Dict[str, Any], document_id: str) -> str:
        """The role a legacy (read-through) reference is presented under."""
        return self.legacy_relationship_role

    def legacy_superseded_by_link(self, role: str) -> bool:
        """Does a canonical link of the same Document hide this legacy reference?

        True for plain legacy arrays: the canonical row is the same fact, re-stated.
        A provenance field (a chronology event's source) is a different fact and
        stays visible beside any link of the same Document.
        """
        return True

    def offers_link(self, context: EntityContext) -> bool:
        """May this loaded target be offered by Link to Record?

        For read-only states a row query cannot express (a parent's status).
        """
        return True

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        raise NotImplementedError

    def context_from_entity(self, entity: Dict[str, Any]) -> EntityContext:
        raise NotImplementedError

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise NotImplementedError

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        return []

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        raise NotImplementedError

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        raise NotImplementedError

    def link_target_query(self) -> Dict[str, Any]:
        """Extra row filter for "Link to Record" candidates (rows that may take a new link)."""
        return {}

    async def guard_relationship_remove(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        """May an existing link be removed from this target? Default: yes.

        A register whose record can become read-only (an archived Hindrance)
        refuses here, so read-only means its evidence can be neither added nor
        removed. Adding is refused by ``guard_relationship_write``.
        """
        return True


class ClaimEntityAdapter(EntityAdapter):
    target_type = "claim"
    #: CL-4A: bound by the selected navbar project on the shared relationship routes.
    active_scope_enforced = True
    link_to_record = True
    link_manage_permissions = (Permissions.CLAIM_EDIT,)
    target_collection = "claims"
    target_label_fields = ('claim_ref', 'title')
    legacy_relationship_role = "supporting_document"

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        claim = None
        for candidate in document_id_candidates(target_id):
            claim = await db.claims.find_one({"_id": candidate})
            if claim:
                break
        if not claim:
            return None
        return self.context_from_entity(claim)

    def context_from_entity(self, claim: Dict[str, Any]) -> EntityContext:
        organization_id = str(claim.get("organization_id") or "")
        project_id = str(claim.get("project_id") or "")
        claim_id = str(claim.get("_id") or "")
        label = str(claim.get("claim_ref") or claim.get("title") or claim_id)
        return EntityContext(
            target_type=self.target_type,
            target_id=claim_id,
            entity=claim,
            organization_id=organization_id,
            project_id=project_id,
            view_permission=Permissions.CLAIM_VIEW,
            manage_permission=Permissions.CLAIM_EDIT,
            delete_permission=Permissions.CLAIM_DELETE,
            allowed_roles=CLAIM_DOCUMENT_ROLES,
            label=label,
            route=f"/claims/{claim_id}",
            frozen=bool(claim.get("evidence_frozen_at")),
        )

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        result = await db.claims.update_one(
            {"_id": context.entity.get("_id"), "evidence_frozen_at": None},
            {
                "$set": {
                    "evidence_frozen_at": frozen_at,
                    "evidence_frozen_by": actor_id,
                    "evidence_freeze_reason": reason,
                },
                "$unset": {"linked_document_ids": ""},
            },
            session=session,
        )
        if not getattr(result, "matched_count", 0):
            raise RuntimeError("Claim evidence was concurrently frozen")

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        cursor = db.claims.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "linked_document_ids": document_id,
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.claims.update_one(
            {
                "_id": context.entity.get("_id"),
                "evidence_frozen_at": None,
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.claims.delete_one(
            {
                "_id": context.entity.get("_id"),
                "evidence_frozen_at": None,
            },
            session=session,
        )
        return bool(getattr(result, "deleted_count", 0))


class IPCBillEntityAdapter(EntityAdapter):
    """Canonical relationship boundary for the current parent-level IPC model.

    Submission, certification and payment are embedded lifecycle data today;
    they do not have stable entity IDs.  The IPC record therefore remains the
    target and its relationship role carries the evidence meaning.
    """

    target_type = "ipc_bill"
    #: CL-4A: bound by the selected navbar project on the shared relationship routes.
    active_scope_enforced = True
    link_to_record = True
    link_manage_permissions = (Permissions.IPC_EDIT,)
    target_collection = "ipc_bills"
    target_label_fields = ('ipc_number', 'ipc_period')
    supports_freeze = False

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        ipc = None
        for candidate in document_id_candidates(target_id):
            ipc = await db.ipc_bills.find_one({"_id": candidate})
            if ipc:
                break
        if not ipc:
            return None
        return self.context_from_entity(ipc)

    def context_from_entity(self, ipc: Dict[str, Any]) -> EntityContext:
        organization_id = str(ipc.get("organization_id") or "")
        project_id = str(ipc.get("project_id") or "")
        ipc_id = str(ipc.get("_id") or "")
        label = str(ipc.get("ipc_number") or ipc.get("ipc_period") or ipc_id)
        return EntityContext(
            target_type=self.target_type,
            target_id=ipc_id,
            entity=ipc,
            organization_id=organization_id,
            project_id=project_id,
            view_permission=Permissions.IPC_VIEW,
            manage_permission=Permissions.IPC_EDIT,
            delete_permission=Permissions.IPC_DELETE,
            allowed_roles=IPC_DOCUMENT_ROLES,
            label=label,
            route=f"/ipc-bills?ipc_id={ipc_id}",
            frozen=False,
        )

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise RuntimeError("IPC evidence freeze is not supported by the current IPC lifecycle")

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        cursor = db.ipc_bills.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "linked_document_ids": document_id,
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.ipc_bills.update_one(
            {"_id": context.entity.get("_id")},
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.ipc_bills.delete_one(
            {"_id": context.entity.get("_id")},
            session=session,
        )
        return bool(getattr(result, "deleted_count", 0))


class InsuranceEntityAdapter(EntityAdapter):
    """Parent evidence owner for the current event-less Insurance model."""

    target_type = "insurance"
    #: CL-4A: bound by the selected navbar project on the shared relationship routes.
    active_scope_enforced = True
    link_to_record = True
    link_manage_permissions = (Permissions.INSURANCE_EDIT,)
    target_collection = "insurance_policies"
    target_label_fields = ('policy_number', 'insurance_type')
    supports_freeze = False
    legacy_relationship_role = "manual_review"

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        insurance = None
        for candidate in document_id_candidates(target_id):
            insurance = await db.insurance_policies.find_one({"_id": candidate})
            if insurance:
                break
        return self.context_from_entity(insurance) if insurance else None

    def context_from_entity(self, insurance: Dict[str, Any]) -> EntityContext:
        insurance_id = str(insurance.get("_id") or "")
        label = str(
            insurance.get("policy_number")
            or insurance.get("insurance_type")
            or insurance_id
        )
        return EntityContext(
            target_type=self.target_type,
            target_id=insurance_id,
            entity=insurance,
            organization_id=str(insurance.get("organization_id") or ""),
            project_id=str(insurance.get("project_id") or ""),
            view_permission=Permissions.INSURANCE_VIEW,
            manage_permission=Permissions.INSURANCE_EDIT,
            delete_permission=Permissions.INSURANCE_DELETE,
            allowed_roles=INSURANCE_DOCUMENT_ROLES,
            label=label,
            route=f"/insurance?insurance_id={insurance_id}",
            frozen=False,
        )

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        collection = getattr(db, "insurance_policies", None)
        if collection is None:
            return []
        cursor = collection.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "linked_document_ids": document_id,
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise RuntimeError("Insurance evidence freeze is not supported")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.insurance_policies.update_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.insurance_policies.delete_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
            },
            session=session,
        )
        return bool(getattr(result, "deleted_count", 0))


class BankGuaranteeEventEntityAdapter(EntityAdapter):
    """Event-level evidence owner for the Bank Guarantee lifecycle."""

    target_type = "bank_guarantee_event"
    #: CL-4A: bound by the selected navbar project on the shared relationship routes.
    active_scope_enforced = True
    link_to_record = True
    link_manage_permissions = (Permissions.BG_EDIT, Permissions.BG_EXTEND, Permissions.BG_RELEASE)
    target_collection = "bank_guarantee_events"
    supports_freeze = False

    async def link_target_search(
        self,
        db: Any,
        needle: str,
        *,
        organization_id: str,
        project_id: str,
    ) -> Optional[Dict[str, Any]]:
        # The label is "<parent BG number or type or id> · <Event type> <n>":
        # resolve the matching parents in scope first, so an old event of a
        # matching guarantee is found however many events precede it. A search
        # into the composed tail is not narrowed (the label check decides).
        if not needle or _label_tail_search(needle):
            return None
        pattern = {"$regex": re.escape(needle), "$options": "i"}
        parents = await _matching_parent_ids(
            db.bank_guarantees,
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "$or": [{"bg_number": pattern}, {"bg_type": pattern}, {"_id": pattern}],
            },
        )
        # Event types are stored snake_case and rendered title-cased.
        type_pattern = {"$regex": re.escape(needle.replace(" ", "_")), "$options": "i"}
        return {"$or": [{"bank_guarantee_id": {"$in": parents}}, {"event_type": type_pattern}]}

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        event = None
        for candidate in document_id_candidates(target_id):
            event = await db.bank_guarantee_events.find_one({"_id": candidate})
            if event:
                break
        if not event:
            return None
        bg = None
        for candidate in document_id_candidates(str(event.get("bank_guarantee_id") or "")):
            bg = await db.bank_guarantees.find_one({"_id": candidate})
            if bg:
                break
        if not bg:
            return None
        event_org = str(event.get("organization_id") or "")
        event_project = str(event.get("project_id") or "")
        if (
            str(bg.get("organization_id") or "") != event_org
            or str(bg.get("project_id") or "") != event_project
            or str(event.get("bank_guarantee_id") or "") != str(bg.get("_id") or "")
        ):
            return None
        return self.context_from_entity({**event, "_parent_bank_guarantee": bg})

    def context_from_entity(self, entity: Dict[str, Any]) -> EntityContext:
        bg = entity.get("_parent_bank_guarantee") or {}
        raw_event_type = entity.get("event_type")
        event_type = (
            raw_event_type.value
            if raw_event_type is not None and hasattr(raw_event_type, "value")
            else str(raw_event_type or "")
        )
        event_id = str(entity.get("_id") or "")
        bg_id = str(entity.get("bank_guarantee_id") or bg.get("_id") or "")
        sequence = int(entity.get("sequence") or 0)
        revision_number = entity.get("revision_number")
        bg_label = str(bg.get("bg_number") or bg.get("bg_type") or bg_id)
        event_label = event_type.replace("_", " ").title()
        manage_permission = {
            "extension": Permissions.BG_EXTEND,
            "release": Permissions.BG_RELEASE,
        }.get(event_type, Permissions.BG_EDIT)
        return EntityContext(
            target_type=self.target_type,
            target_id=event_id,
            entity=entity,
            organization_id=str(entity.get("organization_id") or ""),
            project_id=str(entity.get("project_id") or ""),
            view_permission=Permissions.BG_VIEW,
            manage_permission=manage_permission,
            delete_permission=Permissions.BG_DELETE,
            allowed_roles=BANK_GUARANTEE_EVENT_ROLES.get(event_type, frozenset()),
            label=(
                f"{bg_label} · {event_label} {int(revision_number)}"
                if event_type == "extension" and revision_number is not None
                else f"{bg_label} · {event_label} {sequence}"
            ),
            route=f"/bank-guarantees?bg_id={bg_id}&event_id={event_id}",
            frozen=False,
            parent_type="bank_guarantee",
            parent_id=bg_id,
        )

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise RuntimeError("Bank Guarantee event evidence freeze is not supported")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.bank_guarantee_events.update_one(
            {
                "_id": context.entity.get("_id"),
                "bank_guarantee_id": context.parent_id,
                "organization_id": context.organization_id,
                "project_id": context.project_id,
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        return False


class BankGuaranteeLegacyEntityAdapter(EntityAdapter):
    """Compatibility-only parent view for ambiguous legacy BG arrays.

    Native evidence must target a stable ``bank_guarantee_event``.  This
    adapter deliberately exposes no writable roles; it exists only so old
    parent arrays can be authority-filtered and labelled for manual review.
    """

    target_type = "bank_guarantee"
    #: CL-4A: bound by the selected navbar project on the shared relationship routes.
    active_scope_enforced = True
    target_collection = "bank_guarantees"
    supports_freeze = False
    legacy_relationship_role = "manual_review"

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        bg = None
        for candidate in document_id_candidates(target_id):
            bg = await db.bank_guarantees.find_one({"_id": candidate})
            if bg:
                break
        return self.context_from_entity(bg) if bg else None

    def context_from_entity(self, bg: Dict[str, Any]) -> EntityContext:
        bg_id = str(bg.get("_id") or "")
        label = str(bg.get("bg_number") or bg.get("bg_type") or bg_id)
        return EntityContext(
            target_type=self.target_type,
            target_id=bg_id,
            entity=bg,
            organization_id=str(bg.get("organization_id") or ""),
            project_id=str(bg.get("project_id") or ""),
            view_permission=Permissions.BG_VIEW,
            manage_permission=Permissions.BG_EDIT,
            delete_permission=Permissions.BG_DELETE,
            allowed_roles=frozenset(),
            label=label,
            route=f"/bank-guarantees?bg_id={bg_id}",
            frozen=False,
        )

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        cursor = db.bank_guarantees.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "linked_document_ids": document_id,
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise RuntimeError("Bank Guarantee parent evidence freeze is not supported")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        return False

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        return False


class KeyDateAchievementEntityAdapter(EntityAdapter):
    """Stable event-level evidence owner for a milestone achievement."""

    target_type = "key_date_achievement"
    #: CL-4A: bound by the selected navbar project on the shared relationship routes.
    active_scope_enforced = True
    link_to_record = True
    link_manage_permissions = (Permissions.KEYDATE_ACHIEVEMENT,)
    target_label_suffix = " · Achievement"
    target_collection = "key_date_achievements"
    supports_freeze = False
    legacy_relationship_role = "manual_review"

    async def link_target_search(
        self,
        db: Any,
        needle: str,
        *,
        organization_id: str,
        project_id: str,
    ) -> Optional[Dict[str, Any]]:
        # The label is the parent Key Date's reference or title: resolve the
        # matching Key Dates in scope, then take their achievements.
        if not needle or needle in self.target_label_suffix.lower() or _label_tail_search(needle):
            return None
        pattern = {"$regex": re.escape(needle), "$options": "i"}
        parents = await _matching_parent_ids(
            db.key_date_milestones,
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "$or": [{"milestone_ref": pattern}, {"title": pattern}, {"_id": pattern}],
            },
        )
        return {"milestone_id": {"$in": parents}}

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        achievement = await db.key_date_achievements.find_one({"_id": str(target_id)})
        if not achievement:
            return None
        milestone = await db.key_date_milestones.find_one(
            {"_id": str(achievement.get("milestone_id") or "")}
        )
        if not milestone:
            return None
        if (
            str(achievement.get("milestone_id") or "") != str(milestone.get("_id") or "")
            or str(achievement.get("organization_id") or "")
            != str(milestone.get("organization_id") or "")
            or str(achievement.get("project_id") or "")
            != str(milestone.get("project_id") or "")
        ):
            return None
        return self.context_from_entity({**achievement, "_parent_key_date": milestone})

    def context_from_entity(self, entity: Dict[str, Any]) -> EntityContext:
        milestone = entity.get("_parent_key_date") or {}
        achievement_id = str(entity.get("_id") or "")
        milestone_id = str(entity.get("milestone_id") or milestone.get("_id") or "")
        label = str(
            milestone.get("milestone_ref")
            or milestone.get("title")
            or milestone_id
        )
        return EntityContext(
            target_type=self.target_type,
            target_id=achievement_id,
            entity=entity,
            organization_id=str(entity.get("organization_id") or ""),
            project_id=str(entity.get("project_id") or ""),
            view_permission=Permissions.KEYDATE_VIEW,
            manage_permission=Permissions.KEYDATE_ACHIEVEMENT,
            delete_permission=Permissions.KEYDATE_DELETE,
            allowed_roles=KEY_DATE_ACHIEVEMENT_ROLES,
            label=f"{label} · Achievement",
            route=f"/key-dates/{milestone_id}?achievement_id={achievement_id}",
            frozen=False,
            parent_type="key_date",
            parent_id=milestone_id,
        )

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        try:
            collection = db.key_date_achievements
        except AttributeError:
            return []
        cursor = collection.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "linked_document_ids": document_id,
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise RuntimeError("Key Date achievement evidence freeze is not supported")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.key_date_achievements.update_one(
            {
                "_id": context.entity.get("_id"),
                "milestone_id": context.parent_id,
                "organization_id": context.organization_id,
                "project_id": context.project_id,
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        return False


class EOTSubmissionEntityAdapter(EntityAdapter):
    """Canonical event owner for one project-level Contractor EOT submission."""

    target_type = "eot_submission"
    #: CL-4A: bound by the selected navbar project on the shared relationship routes.
    active_scope_enforced = True
    link_to_record = True
    link_manage_permissions = (Permissions.KEYDATE_EOT_SUBMIT,)
    target_label_suffix = " · Contractor Submission"
    target_collection = "key_date_eot_submissions"
    target_label_fields = ('revision_label',)
    legacy_relationship_role = "manual_review"
    freeze_requires_lifecycle_orchestration = True

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        submission = await db.key_date_eot_submissions.find_one({"_id": str(target_id)})
        if not submission:
            return None
        scope = {
            "organization_id": str(submission.get("organization_id") or ""),
            "project_id": str(submission.get("project_id") or ""),
            "contract_id": str(submission.get("contract_id") or "primary"),
        }
        baseline = await db.key_date_baselines.find_one(
            {**scope, "status": "frozen"}
        )
        if not baseline:
            return None
        item = await db.key_date_eot_submission_items.find_one(
            {"eot_submission_id": str(submission.get("_id") or "")}
        )
        return self.context_from_entity(
            {**submission, "_parent_baseline": baseline, "_has_items": bool(item)}
        )

    def context_from_entity(self, entity: Dict[str, Any]) -> EntityContext:
        submission_id = str(entity.get("_id") or "")
        project_id = str(entity.get("project_id") or "")
        label = str(entity.get("revision_label") or submission_id)
        return EntityContext(
            target_type=self.target_type,
            target_id=submission_id,
            entity=entity,
            organization_id=str(entity.get("organization_id") or ""),
            project_id=project_id,
            view_permission=Permissions.KEYDATE_VIEW,
            manage_permission=Permissions.KEYDATE_EOT_SUBMIT,
            delete_permission=Permissions.KEYDATE_DELETE,
            allowed_roles=EOT_SUBMISSION_ROLES,
            label=f"{label} · Contractor Submission",
            route=f"/key-dates?project_id={project_id}&submission_id={submission_id}",
            frozen=bool(entity.get("locked_at")),
            parent_type="project",
            parent_id=project_id,
            freeze_permission=Permissions.KEYDATE_EOT_LOCK_SUBMISSION,
        )

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        try:
            collection = db.key_date_eot_submissions
        except AttributeError:
            return []
        cursor = collection.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "linked_document_ids": document_id,
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        submission = await db.key_date_eot_submissions.find_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
            },
            session=session,
        )
        item = await db.key_date_eot_submission_items.find_one(
            {"eot_submission_id": str(context.entity.get("_id") or "")},
            session=session,
        )
        if not submission:
            raise RuntimeError("EOT submission changed before it could be locked")
        if not item:
            raise RuntimeError("Add at least one affected milestone before locking the EOT submission")
        if not str(submission.get("contractor_letter_reference") or "").strip():
            raise RuntimeError("Contractor Letter Reference is required before locking the submission")
        if not submission.get("contractor_submission_date"):
            raise RuntimeError("Contractor Submission Date is required before locking the submission")
        lifecycle_revision = int(submission.get("lifecycle_revision") or 0)
        result = await db.key_date_eot_submissions.update_one(
            {
                "_id": submission.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
                "locked_at": None,
                "status": {"$in": ["draft", "submitted"]},
                "lifecycle_revision": lifecycle_revision or {"$in": [None, 0]},
            },
            {
                "$set": {
                    "status": "locked",
                    "locked_at": frozen_at,
                    "locked_by": actor_id,
                    "evidence_freeze_reason": reason,
                },
                "$unset": {"linked_document_ids": ""},
                "$inc": {"lifecycle_revision": 1},
            },
            session=session,
        )
        if not getattr(result, "matched_count", 0):
            raise RuntimeError("EOT submission was concurrently locked")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.key_date_eot_submissions.update_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
                "locked_at": None,
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        return False


class EOTDeterminationEntityAdapter(EntityAdapter):
    """Canonical event owner for one Engineer/Employer EOT determination."""

    target_type = "eot_determination"
    #: CL-4A: bound by the selected navbar project on the shared relationship routes.
    active_scope_enforced = True
    link_to_record = True
    link_manage_permissions = (Permissions.KEYDATE_EOT_DETERMINE,)
    target_label_suffix = " · Determination"
    target_collection = "key_date_eot_determinations"
    target_label_fields = ('determination_reference',)
    legacy_relationship_role = "manual_review"
    freeze_requires_lifecycle_orchestration = True

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        determination = await db.key_date_eot_determinations.find_one(
            {"_id": str(target_id)}
        )
        if not determination:
            return None
        scope = {
            "organization_id": str(determination.get("organization_id") or ""),
            "project_id": str(determination.get("project_id") or ""),
            "contract_id": str(determination.get("contract_id") or "primary"),
        }
        baseline = await db.key_date_baselines.find_one(
            {**scope, "status": "frozen"}
        )
        if not baseline:
            return None
        items = [
            row
            async for row in db.key_date_eot_determination_items.find(
                {"determination_id": str(determination.get("_id") or "")}
            )
        ]
        return self.context_from_entity(
            {**determination, "_parent_baseline": baseline, "_items": items}
        )

    def context_from_entity(self, entity: Dict[str, Any]) -> EntityContext:
        determination_id = str(entity.get("_id") or "")
        project_id = str(entity.get("project_id") or "")
        label = str(entity.get("determination_reference") or determination_id)
        return EntityContext(
            target_type=self.target_type,
            target_id=determination_id,
            entity=entity,
            organization_id=str(entity.get("organization_id") or ""),
            project_id=project_id,
            view_permission=Permissions.KEYDATE_VIEW,
            manage_permission=Permissions.KEYDATE_EOT_DETERMINE,
            delete_permission=Permissions.KEYDATE_DELETE,
            allowed_roles=EOT_DETERMINATION_ROLES,
            label=f"{label} · Determination",
            route=f"/key-dates?project_id={project_id}&determination_id={determination_id}",
            frozen=bool(entity.get("frozen_at")),
            parent_type="project",
            parent_id=project_id,
            freeze_permission=Permissions.KEYDATE_EOT_FREEZE_DETERMINATION,
        )

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        try:
            collection = db.key_date_eot_determinations
        except AttributeError:
            return []
        cursor = collection.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "linked_document_ids": document_id,
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        determination = await db.key_date_eot_determinations.find_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
            },
            session=session,
        )
        if not determination:
            raise RuntimeError("EOT determination changed before it could be frozen")
        status = str(determination.get("status") or "")
        if status not in {
            "granted",
            "partially_granted",
            "rejected",
            "no_extension",
            "superseded",
        }:
            raise RuntimeError("Set a final determination status before freezing")
        if not str(determination.get("determination_reference") or "").strip():
            raise RuntimeError("Determination Reference is required before freezing")
        if not determination.get("determination_date"):
            raise RuntimeError("Determination Date is required before freezing")
        items = [
            row
            async for row in db.key_date_eot_determination_items.find(
                {"determination_id": str(determination.get("_id") or "")},
                session=session,
            )
        ]
        if not items:
            raise RuntimeError("Add at least one milestone determination before freezing")
        if status in {"granted", "partially_granted"} and not str(
            determination.get("approval_grant_reference") or ""
        ).strip():
            raise RuntimeError("Approval / Grant Reference is required for a granted determination")
        baseline = await db.key_date_baselines.find_one(
            {
                "organization_id": context.organization_id,
                "project_id": context.project_id,
                "contract_id": str(determination.get("contract_id") or "primary"),
                "status": "frozen",
            },
            session=session,
        )
        baseline_ids = {
            str(item.get("key_date_id") or "")
            for item in (baseline or {}).get("items") or []
        }
        for item in items:
            if str(item.get("key_date_id") or "") not in baseline_ids:
                raise RuntimeError(
                    f"Milestone {item.get('milestone_ref')} is not part of the frozen Original baseline"
                )
            if str(item.get("determination_result") or "") in {
                "granted",
                "partially_granted",
            } and not item.get("eot_granted_date"):
                raise RuntimeError(
                    f"Granted Date is required for {item.get('milestone_ref')}"
                )
        lifecycle_revision = int(determination.get("lifecycle_revision") or 0)
        result = await db.key_date_eot_determinations.update_one(
            {
                "_id": determination.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
                "frozen_at": None,
                "lifecycle_revision": lifecycle_revision or {"$in": [None, 0]},
            },
            {
                "$set": {
                    "frozen_at": frozen_at,
                    "frozen_by": actor_id,
                    "evidence_freeze_reason": reason,
                },
                "$unset": {"linked_document_ids": ""},
                "$inc": {"lifecycle_revision": 1},
            },
            session=session,
        )
        if not getattr(result, "matched_count", 0):
            raise RuntimeError("EOT determination was concurrently frozen")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.key_date_eot_determinations.update_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
                "frozen_at": None,
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        return False


class VariationEntityAdapter(EntityAdapter):
    """Variation Register records as relationship targets (CL-2).

    The Variation lifecycle has no evidence-freeze step, so there is no freeze
    contract. Deleting a Variation is a register act that goes through
    ``DocumentRelationshipService.delete_target`` so its links are removed and
    audited in the same transaction.
    """

    target_type = "variation"
    link_to_record = True
    link_manage_permissions = (Permissions.VARIATION_EDIT,)
    target_collection = "variations"
    target_label_fields = ('variation_number',)
    active_scope_enforced = True
    supports_freeze = False
    legacy_relationship_role = "manual_review"

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        variation = None
        for candidate in document_id_candidates(target_id):
            variation = await db.variations.find_one({"_id": candidate})
            if variation:
                break
        return self.context_from_entity(variation) if variation else None

    def context_from_entity(self, variation: Dict[str, Any]) -> EntityContext:
        variation_id = str(variation.get("_id") or "")
        label = str(variation.get("variation_number") or variation_id)
        return EntityContext(
            target_type=self.target_type,
            target_id=variation_id,
            entity=variation,
            organization_id=str(variation.get("organization_id") or ""),
            project_id=str(variation.get("project_id") or ""),
            view_permission=Permissions.VARIATION_VIEW,
            manage_permission=Permissions.VARIATION_EDIT,
            delete_permission=Permissions.VARIATION_DELETE,
            allowed_roles=VARIATION_DOCUMENT_ROLES,
            label=label,
            route=f"/variations?variation_id={quote(variation_id, safe='')}",
            frozen=False,
        )

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        try:
            collection = db.variations
        except AttributeError:
            return []
        cursor = collection.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "linked_document_ids": document_id,
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise RuntimeError("Variation evidence freeze is not supported")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        # Fenced on the scope `load` read: a Variation moved to another project
        # between load and commit refuses the write.
        result = await db.variations.update_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.variations.delete_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
            },
            session=session,
        )
        return bool(getattr(result, "deleted_count", 0))


class ContractDocumentEntityAdapter(EntityAdapter):
    """Contract instruments as relationship targets -- PROJECT-SCOPED ONLY.

    An organisation-scoped instrument is loaded with an empty project_id, so
    the generic project-less refusal in DocumentRelationshipService turns it
    away with a 409. That refusal is deliberately left to the generic service:
    no Contract-specific gate is added, and none is needed.

    The project comes from the instrument's own structural anchor
    (scope_project_id) and from nowhere else -- never from an applicability
    row, the request scope, the canonical Document, or a caller parameter. An
    organisation-scoped instrument that currently applies to exactly one project
    is still refused: applicability can be withdrawn, and a link whose ownership
    was borrowed from it would then be undefined.

    Loading a target is a read. It creates no lifecycle event, resolves no
    classification, and never touches document_id.

    Writing a link (CL-1) bumps only ``document_relationship_revision``, fenced on
    the same structural anchor ``load`` read, so an instrument re-scoped between
    load and commit refuses the write instead of gaining a link in its old
    project. Deleting an instrument is a Contract Master lifecycle act, never a
    side effect of relationship management, so ``delete`` always refuses.
    """

    target_type = "contract_document"
    #: CL-4A: bound by the selected navbar project on the shared relationship routes.
    active_scope_enforced = True
    target_collection = "contract_documents"
    supports_freeze = False

    @staticmethod
    def _route(record: Dict[str, Any]) -> str:
        """Deep link to the instrument's canonical Document in the contract viewer.

        There is no SPA route keyed by ContractDocument id (the old
        ``/contract-documents/{id}`` never existed, and the Contract Master
        workspace does not read one). The contract viewer route, keyed by
        Document id, is live and loads the instrument's own content, and
        ContractDocument.document_id is its canonical identity.
        """
        document_id = str(record.get("document_id") or "")
        return f"/contracts/viewer/{quote(document_id, safe='')}" if document_id else "/contracts/viewer"

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise RuntimeError("Contract Document evidence freeze is not supported")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db["contract_documents"].update_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "scope_level": "project",
                "scope_project_id": context.project_id,
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        return False

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        record = None
        for candidate in document_id_candidates(target_id):
            record = await db["contract_documents"].find_one({"_id": candidate})
            if record:
                break
        if not record:
            return None
        return self.context_from_entity(record)

    def context_from_entity(self, record: Dict[str, Any]) -> EntityContext:
        contract_document_id = str(record.get("_id") or "")
        organization_id = str(record.get("organization_id") or "")

        # The discriminator decides, not the presence of a stored value: a stray
        # scope_project_id on an organisation-scoped record is not an anchor.
        is_project_scoped = str(record.get("scope_level") or "") == "project"
        project_id = str(record.get("scope_project_id") or "") if is_project_scoped else ""

        label = str(record.get("contract_document_type") or contract_document_id)
        return EntityContext(
            target_type=self.target_type,
            target_id=contract_document_id,
            entity=record,
            organization_id=organization_id,
            project_id=project_id,
            view_permission=Permissions.CONTRACT_MASTER_VIEW,
            manage_permission=Permissions.CONTRACT_MASTER_MANAGE,
            delete_permission=Permissions.CONTRACT_MASTER_MANAGE,
            allowed_roles=CONTRACT_DOCUMENT_ROLES,
            label=label,
            route=self._route(record),
            frozen=False,
        )


class DelayEventEntityAdapter(EntityAdapter):
    """Hindrance & Constraint Register entries (stored in `delay_events`).

    Legacy `linked_document_ids` were only ever supporting documents, so the
    read-through role is unambiguous. An archived entry is read-only: the write
    guard refuses new evidence until it is restored. There is no delete.
    """

    target_type = "delay_event"
    link_to_record = True
    link_manage_permissions = (Permissions.HINDRANCE_EDIT,)
    target_collection = "delay_events"
    target_label_fields = ("hindrance_ref", "delay_ref", "title")
    active_scope_enforced = True
    supports_freeze = False
    legacy_relationship_role = "supporting_document"

    def link_target_query(self) -> Dict[str, Any]:
        # An archived entry is read-only (the write guard refuses it), so it is
        # never offered as a Link-to-Record candidate.
        return {"archived_at": None}

    async def guard_relationship_remove(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        # Read-only means read-only in both directions: an archived entry's
        # evidence cannot be removed either, until it is restored.
        return not context.entity.get("archived_at")

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        row = None
        for candidate in document_id_candidates(target_id):
            row = await db.delay_events.find_one({"_id": candidate})
            if row:
                break
        return self.context_from_entity(row) if row else None

    def context_from_entity(self, row: Dict[str, Any]) -> EntityContext:
        item_id = str(row.get("_id") or "")
        label = str(row.get("hindrance_ref") or row.get("delay_ref") or row.get("title") or item_id)
        return EntityContext(
            target_type=self.target_type,
            target_id=item_id,
            entity=row,
            organization_id=str(row.get("organization_id") or ""),
            project_id=str(row.get("project_id") or ""),
            view_permission=Permissions.HINDRANCE_VIEW,
            manage_permission=Permissions.HINDRANCE_EDIT,
            delete_permission=Permissions.HINDRANCE_ARCHIVE,
            allowed_roles=HINDRANCE_DOCUMENT_ROLES,
            label=label,
            route=f"/hindrances/{item_id}",
            frozen=False,
        )

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        collection = getattr(db, "delay_events", None)
        if collection is None:
            return []
        cursor = collection.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "linked_document_ids": document_id,
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise RuntimeError("Hindrance evidence freeze is not supported")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        result = await db.delay_events.update_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
                "archived_at": None,
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        return False


class ProgrammeMilestoneEntityAdapter(EntityAdapter):
    """Programme Milestones (``programme_milestones``) as relationship targets (CL-3B).

    Scope is the row's own ``organization_id`` / ``project_id`` - the register
    requires both on create. There is no delete, archive or read-only state in
    the register: a ``superseded`` milestone is a programme status, not an
    archive, and keeps taking evidence. Legacy ``linked_document_ids`` are the
    register's G31-certified "supporting documents" and read through as such.
    """

    target_type = "programme_milestone"
    target_collection = "programme_milestones"
    target_label_fields = ("milestone_ref", "title")
    link_to_record = True
    link_manage_permissions = (Permissions.EVIDENCE_GRAPH_MANAGE,)
    active_scope_enforced = True
    legacy_blocks_document_deletion = False
    supports_freeze = False
    legacy_relationship_role = "supporting_document"

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        row = None
        for candidate in document_id_candidates(target_id):
            row = await db.programme_milestones.find_one({"_id": candidate})
            if row:
                break
        return self.context_from_entity(row) if row else None

    def context_from_entity(self, row: Dict[str, Any]) -> EntityContext:
        item_id = str(row.get("_id") or "")
        reference = str(row.get("milestone_ref") or "").strip()
        title = str(row.get("title") or "").strip()
        label = " · ".join(part for part in (reference, title) if part) or item_id
        return EntityContext(
            target_type=self.target_type,
            target_id=item_id,
            entity=row,
            organization_id=str(row.get("organization_id") or ""),
            project_id=str(row.get("project_id") or ""),
            view_permission=Permissions.EVIDENCE_GRAPH_VIEW,
            manage_permission=Permissions.EVIDENCE_GRAPH_MANAGE,
            delete_permission=Permissions.EVIDENCE_GRAPH_MANAGE,
            allowed_roles=PROGRAMME_MILESTONE_DOCUMENT_ROLES,
            label=label,
            route=f"/programme-milestones/{quote(item_id, safe='')}",
            frozen=False,
        )

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        collection = getattr(db, "programme_milestones", None)
        if collection is None:
            return []
        cursor = collection.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "linked_document_ids": document_id,
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise RuntimeError("Programme milestone evidence freeze is not supported")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        # Fenced on the scope `load` read: a milestone moved to another project
        # between load and commit refuses the write.
        result = await db.programme_milestones.update_one(
            {
                "_id": context.entity.get("_id"),
                "organization_id": context.organization_id,
                "project_id": context.project_id,
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(result, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        # The register has no delete. Refusing here makes `delete_target`
        # answer 409 instead of removing a record the register cannot remove.
        return False


class ChronologyEventEntityAdapter(EntityAdapter):
    """Chronology events (``matter_chronology_events``) as relationship targets (CL-3B).

    The event, not the chronology, is the evidence owner: ``source_document_id``
    and ``related_document_ids`` live on the event, and ``chronology_event`` is
    already the event's name in ``publication_policy``.

    Structural scope is the PARENT chronology's - the chronology routes authorize
    the parent and anchor every event to it
    (``ChronologyService._apply_chronology_authority_scope``). An event whose own
    organisation/project contradicts its parent does not load (integrity), and
    neither does an event of a deleted chronology. An archived chronology is
    read-only: its events take and lose no evidence and are not offered.

    Labels come from the publication-safe projection: a title lifted from a
    source Document that is no longer consumable is never shown as a label.
    """

    target_type = "chronology_event"
    target_collection = "matter_chronology_events"
    # Only fields the publication-safe projection governs: `letter_no` may be
    # lifted from the source Document's text and is not withheld, so it is not
    # searchable here.
    target_label_fields = ("title",)
    link_to_record = True
    link_manage_permissions = (Permissions.CHRONOLOGY_EDIT,)
    active_scope_enforced = True
    legacy_blocks_document_deletion = False
    supports_freeze = False
    legacy_relationship_role = "supporting_document"

    async def _parent(self, db: Any, event: Dict[str, Any], *, session: Any = None) -> Optional[Dict[str, Any]]:
        chronology_id = event.get("chronology_id")
        if not chronology_id:
            return None
        return await db.matter_chronologies.find_one(
            {"_id": chronology_id, "deleted_at": {"$exists": False}}, session=session
        )

    async def load(self, db: Any, target_id: str) -> Optional[EntityContext]:
        from .publication_policy import safe_event_records

        event = await db.matter_chronology_events.find_one({"_id": str(target_id)})
        if not event:
            return None
        chronology = await self._parent(db, event)
        if not chronology:
            return None
        for field in ("organization_id", "project_id"):
            own = event.get(field)
            if own not in (None, "") and str(own) != str(chronology.get(field) or ""):
                return None
        served = (
            await safe_event_records(db, [event], ("description", "title"), span_fields=("source_spans",))
        )[0]
        return self.context_from_entity({**served, "_parent_chronology": chronology})

    def context_from_entity(self, entity: Dict[str, Any]) -> EntityContext:
        chronology = entity.get("_parent_chronology") or {}
        event_id = str(entity.get("_id") or "")
        chronology_id = str(entity.get("chronology_id") or chronology.get("_id") or "")
        title = str(entity.get("title") or "").strip() or "Untitled event"
        event_date = entity.get("event_date")
        date_text = (
            event_date.date().isoformat() if isinstance(event_date, datetime) else str(entity.get("date_text") or "")
        )
        label = " · ".join(part for part in (date_text, title) if part)
        return EntityContext(
            target_type=self.target_type,
            target_id=event_id,
            entity=entity,
            organization_id=str(chronology.get("organization_id") or ""),
            project_id=str(chronology.get("project_id") or ""),
            view_permission=Permissions.CHRONOLOGY_VIEW,
            manage_permission=Permissions.CHRONOLOGY_EDIT,
            delete_permission=Permissions.CHRONOLOGY_ADMIN,
            allowed_roles=CHRONOLOGY_EVENT_DOCUMENT_ROLES,
            label=label,
            route=(
                f"/chronology/{quote(chronology_id, safe='')}"
                f"?event_id={quote(event_id, safe='')}"
            ),
            frozen=False,
            parent_type="matter_chronology",
            parent_id=chronology_id,
        )

    def legacy_document_ids(self, entity: Dict[str, Any]) -> list[str]:
        # The source first: when a Document is both the source and a related
        # document (extraction writes it to both), it reads through once, as
        # the source.
        ordered = [entity.get("source_document_id"), *(entity.get("related_document_ids") or [])]
        return list(dict.fromkeys(str(item) for item in ordered if str(item or "")))

    def legacy_role_for(self, entity: Dict[str, Any], document_id: str) -> str:
        source = str(entity.get("source_document_id") or "")
        if source and source == str(document_id):
            return CHRONOLOGY_SOURCE_DOCUMENT_ROLE
        return self.legacy_relationship_role

    def legacy_superseded_by_link(self, role: str) -> bool:
        return role != CHRONOLOGY_SOURCE_DOCUMENT_ROLE

    @staticmethod
    def _read_only(context: EntityContext) -> bool:
        return str((context.entity.get("_parent_chronology") or {}).get("status") or "") == "archived"

    def offers_link(self, context: EntityContext) -> bool:
        return not self._read_only(context)

    async def link_target_scope(
        self,
        db: Any,
        *,
        organization_id: str,
        project_id: str,
    ) -> Dict[str, Any]:
        # Scoped by the parent: an event row may predate the parent-scope anchor
        # and carry no scope of its own. `load` re-derives and checks it.
        cursor = db.matter_chronologies.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "deleted_at": {"$exists": False},
                "status": {"$ne": "archived"},
            },
            {"_id": 1},
        )
        return {"chronology_id": {"$in": [row.get("_id") async for row in cursor]}}

    async def guard_relationship_remove(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        return not self._read_only(context)

    async def legacy_targets_for_document(
        self,
        db: Any,
        *,
        document_id: str,
        organization_id: str,
        project_id: str,
        session: Any = None,
    ) -> list[str]:
        # Scoped by the parent: events predating the parent-scope anchor may carry
        # no scope of their own, so the candidates are the events of the
        # Document's own (live) chronologies. `list_for_document` still loads each
        # one and keeps it only when its structural scope is the Document's.
        collection = getattr(db, "matter_chronology_events", None)
        chronologies = getattr(db, "matter_chronologies", None)
        if collection is None or chronologies is None:
            return []
        parents = [
            row.get("_id")
            async for row in chronologies.find(
                {"organization_id": organization_id, "project_id": project_id, "deleted_at": {"$exists": False}},
                session=session,
            )
        ]
        if not parents:
            return []
        cursor = collection.find(
            {
                "chronology_id": {"$in": parents},
                "$or": [{"source_document_id": document_id}, {"related_document_ids": document_id}],
            },
            session=session,
        )
        return [str(row.get("_id")) async for row in cursor if row.get("_id")]

    async def freeze(
        self,
        db: Any,
        context: EntityContext,
        *,
        actor_id: Optional[str],
        frozen_at: Any,
        reason: str,
        session: Any = None,
    ) -> None:
        raise RuntimeError("Chronology event evidence freeze is not supported")

    async def guard_relationship_write(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        # The parent must still be live, in the loaded scope and not archived;
        # the event must still belong to it.
        parent = await db.matter_chronologies.update_one(
            {
                "_id": context.parent_id,
                "organization_id": context.organization_id,
                "project_id": context.project_id,
                "deleted_at": {"$exists": False},
                "status": {"$ne": "archived"},
            },
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        if not getattr(parent, "matched_count", 0):
            return False
        event = await db.matter_chronology_events.update_one(
            {"_id": context.entity.get("_id"), "chronology_id": context.parent_id},
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        return bool(getattr(event, "matched_count", 0))

    async def delete(
        self,
        db: Any,
        context: EntityContext,
        *,
        session: Any = None,
    ) -> bool:
        # Events have no delete of their own. Deleting the chronology retires
        # its events' links (`DocumentRelationshipService.retire_target_links`).
        return False


class EntityAdapterRegistry:
    def __init__(self, adapters: list[EntityAdapter] | None = None) -> None:
        registered = adapters or [
            ClaimEntityAdapter(),
            IPCBillEntityAdapter(),
            InsuranceEntityAdapter(),
            BankGuaranteeEventEntityAdapter(),
            BankGuaranteeLegacyEntityAdapter(),
            KeyDateAchievementEntityAdapter(),
            EOTSubmissionEntityAdapter(),
            EOTDeterminationEntityAdapter(),
            ContractDocumentEntityAdapter(),
            VariationEntityAdapter(),
            DelayEventEntityAdapter(),
            ProgrammeMilestoneEntityAdapter(),
            ChronologyEventEntityAdapter(),
        ]
        self._adapters = {adapter.target_type: adapter for adapter in registered}

    def get(self, target_type: str) -> EntityAdapter:
        normalized = str(target_type or "").strip().lower()
        adapter = self._adapters.get(normalized)
        if adapter is None:
            raise KeyError(normalized)
        return adapter

    def adapters(self) -> tuple[EntityAdapter, ...]:
        return tuple(self._adapters.values())

    def active_scope_adapter(self, target_type: str) -> Optional[EntityAdapter]:
        """The adapter when ``target_type`` is bound by the selected project, else ``None``."""
        adapter = self._adapters.get(str(target_type or "").strip().lower())
        return adapter if adapter is not None and adapter.active_scope_enforced else None
