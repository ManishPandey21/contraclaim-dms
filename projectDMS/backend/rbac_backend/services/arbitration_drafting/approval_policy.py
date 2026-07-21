from __future__ import annotations

from typing import Any, Dict, Iterable

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
    # Authorization remains server-side at the route. When identity role claims
    # are present, the claimed reviewer role must also be one of those claims.
    semantic_roles = set().union(*DEFAULT_GATE_ROLES.values())
    if actual.intersection(semantic_roles) and claimed not in actual:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reviewer role is not assigned to the current user")
    return claimed


def enforce_author_approver_separation(user: Any, authors: Iterable[Any], *, gate: str) -> None:
    actor = str(_actor_id(user) or "")
    material_authors = {str(value) for value in authors if value}
    if actor and actor in material_authors:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Author-approver separation prohibits self-approval at the {gate} gate",
        )
