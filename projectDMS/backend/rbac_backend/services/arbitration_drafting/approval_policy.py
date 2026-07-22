from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Sequence

from fastapi import HTTPException, status

from ...core.config import settings


def _actor_id(user: Any) -> Any:
    if isinstance(user, dict):
        return user.get("id") or user.get("_id") or user.get("email")
    return getattr(user, "id", None) or getattr(user, "email", None)


DEFAULT_GATE_ROLES: Dict[str, frozenset[str]] = {
    "document_selection": frozenset({"legal_reviewer", "contract_reviewer"}),
    "matrix_review": frozenset({"legal_reviewer", "contract_reviewer", "delay_reviewer", "quantum_reviewer"}),
    "readiness": frozenset({"senior_legal_approver"}),
    "plan": frozenset({"senior_legal_approver"}),
    "legal_review": frozenset({"legal_reviewer", "senior_legal_approver"}),
    "draft": frozenset({"senior_legal_approver"}),
    "export": frozenset({"export_authorizer"}),
}

MATRIX_ROLE_CLAIMS: Dict[str, frozenset[str]] = {
    "legal": frozenset({"legal_reviewer", "senior_legal_approver"}),
    "contracts": frozenset({"contract_reviewer"}),
    "delay": frozenset({"delay_reviewer"}),
    "quantum": frozenset({"quantum_reviewer"}),
    "reviewer": DEFAULT_GATE_ROLES["matrix_review"],
}


def _configured_roles(gate: str) -> frozenset[str]:
    raw = str(getattr(settings, "ARBITRATION_REVIEWER_ROLE_MATRIX", "") or "")
    overrides: Dict[str, set[str]] = {}
    for clause in raw.split(";"):
        name, separator, values = clause.partition("=")
        if separator:
            overrides[name.strip().lower()] = {value.strip().lower() for value in values.split(",") if value.strip()}
    configured = overrides.get(gate)
    return frozenset(configured) if configured else DEFAULT_GATE_ROLES.get(gate, frozenset())


def actor_roles(user: Any) -> set[str]:
    if isinstance(user, dict):
        values = user.get("roles") or user.get("role_names") or [user.get("role")]
    else:
        values = getattr(user, "roles", None) or getattr(user, "role_names", None) or [getattr(user, "role", None)]
    if isinstance(values, str):
        values = [values]
    result = set()
    for value in values or []:
        name = getattr(value, "name", None) or getattr(value, "value", None) or value
        if name:
            result.add(str(name).strip().lower())
    return result


def enforce_gate_role(gate: str, claimed_role: str, user: Any) -> str:
    claimed = str(claimed_role or "").strip().lower()
    allowed = _configured_roles(gate)
    if allowed and claimed not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": "Reviewer role is not permitted for this arbitration gate", "gate": gate, "allowed_roles": sorted(allowed)},
        )
    actual = actor_roles(user)
    # Route permission grants access to the operation, not a legal specialty.
    # The semantic gate role must always be present in signed identity claims.
    if claimed not in actual:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reviewer role is not assigned to the current user")
    return claimed


def enforce_matrix_reviewer_role(
    claimed_role: str,
    user: Any,
    assignments: Sequence[Mapping[str, Any]],
) -> str:
    """Resolve a matrix reviewer role from server claims or an assignment.

    A generic arbitration approval permission authorizes access to the route; it
    does not confer a legal/quantum/delay/contracts specialty.  A reviewer must
    therefore either carry the corresponding semantic identity role or be the
    server-recorded assignee for that matrix role.
    """

    claimed = str(claimed_role or "").strip().lower()
    allowed_claims = MATRIX_ROLE_CLAIMS.get(claimed)
    if not allowed_claims:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unknown arbitration matrix reviewer role")
    actual = actor_roles(user)
    if actual.intersection(allowed_claims):
        return claimed

    actor = str(_actor_id(user) or "")
    assigned_roles = {
        str(item.get("reviewer_role") or "").strip().lower()
        for item in assignments or []
        if str(item.get("reviewer_user_id") or "") == actor
        and str(item.get("status") or "assigned") in {"assigned", "in_review"}
    }
    if actor and claimed in assigned_roles:
        return claimed
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="The current user is not assigned or qualified for this matrix reviewer role",
    )


def resolve_gate_role(gate: str, user: Any) -> str:
    """Derive a privileged gate role exclusively from signed identity claims."""

    allowed = _configured_roles(gate)
    actual = actor_roles(user)
    matches = sorted(actual.intersection(allowed))
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"The current user has no server-assigned role for the {gate} gate",
        )
    return matches[0]


def enforce_author_approver_separation(user: Any, authors: Iterable[Any], *, gate: str) -> None:
    actor = str(_actor_id(user) or "")
    material_authors = {str(value) for value in authors if value}
    if actor and actor in material_authors:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Author-approver separation prohibits self-approval at the {gate} gate",
        )
