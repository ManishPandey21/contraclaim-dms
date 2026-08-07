# Authorization — The Canonical Pattern

> One authorization model, deny-by-default. This document is the single source of
> truth for how to authorize a request in the backend. It was produced by the
> Week-1 authorization-consolidation work.

## Single sources of truth

| Concern | Component | Notes |
|---|---|---|
| **Permission (RBAC)** | `PermissionService.user_has_permission(...)` | The only place permission truth is computed. |
| **Tenant scope** | `ScopeService.is_client_scope_allowed(...)` | Deny-by-default org/project membership. |
| **Subscription/entitlement** | `EntitlementService.check_permission_entitlement(...)` | Gates features by plan. |
| **Orchestrating gate** | `PolicyService.authorize(...)` | Combines all three + emits an audit event. |

Everything funnels through these. Do **not** query `db.roles` directly, re-implement
scope checks, or invent per-router authorization logic.

## Decision guide — which tool do I use?

| Situation | Use | Behaviour |
|---|---|---|
| A scoped or mutating endpoint (create/update/delete, or anything tied to an org/project/document) | `await PolicyService.authorize(user, perm, organization_id=..., project_id=...)` or `authorize_document(user, perm, document)` | Raises `403`; checks permission **+ entitlement + scope**; **audits**. |
| A permission-only gate with no tenant scope (e.g. a global admin read) | `Depends(require_permission("perm"))` | Raises `403`; superadmin bypass; audits. |
| A list endpoint that must return only in-scope rows | `build_scope_query(user, ...)` to build the Mongo filter | Returns a query; never returns out-of-scope rows. |
| A boolean check to drive conditional logic (not a hard gate) | `await PolicyService.has_permission(user, perm)` | Returns `bool`; superadmin bypass; admin perms satisfy sub-perms. |

`AuthorizationService.has_permission(...)` is an acceptable boolean predicate where a
controller already holds an `AuthorizationService` (it delegates to
`PermissionService` and emits a permission-check audit). Prefer
`PolicyService.has_permission` for new code.

## Canonical examples

**Scoped mutation (the default for write endpoints):**
```python
@router.post("/documents", response_model=Document)
async def create_document(payload: DocumentCreate,
                          current_user: CurrentUser = Depends(get_current_user),
                          policy: PolicyService = Depends(get_policy_service)):
    await policy.authorize(
        current_user, Permissions.DOCUMENT_UPLOAD,
        resource_type="documents",
        organization_id=payload.organization_id,
        project_id=payload.project_id,
    )
    ...
```

**Document-bound action:**
```python
document = await service.get_document_by_id(id)
await policy.authorize_document(current_user, Permissions.DOCUMENT_DOWNLOAD, document)
```

**List endpoint (scope by query, not by post-filter):**
```python
query = build_scope_query(current_user, organization_id=org, project_id=proj)
cursor = db.documents.find(query)
```

**Permission-only gate (no tenant scope):**
```python
_: bool = Depends(require_permission("dms.report.view"))
```

## Scope contract — what a selection means

`build_scope_query` narrows a listing to what the caller may see. Two inputs
decide the result: the caller's **role tier** and the **active selection**.

### What "selected" means

For the two global roles, `current_user.organization_id` carries the caller's
**active selection — not the organisation their account belongs to**. The auth
layer guarantees this: `get_current_user` clears the home organisation for
Super Admin / Super User, then re-populates the field only from an `X-Org-Id`
that `TenantContextResolver` has validated against the caller's entitlement.
Nothing selected is therefore `None`.

For the tenant-bound roles the field keeps its ordinary meaning — the account's
own organisation — and the server pins them to it, rejecting any mismatched
request rather than silently rewriting it.

A selection may arrive either as the explicit `organization_id=` / `project_id=`
argument or on the actor. Both must produce the same filter; a route that reads
only the argument will serve consolidated data to a caller who has selected an
organisation.

### Decision table

| Role tier | Nothing selected | Organisation selected | Org + project selected |
|---|---|---|---|
| **Super Admin** | everything (`{}`) | that organisation | that org + project |
| **Super User** | consolidated across *assigned* orgs | that organisation (deny if unassigned) | that org + project |
| **Org Admin / Org User** | whole own organisation | own org, deny if a different one is asked for | own org + that project |
| **Project Admin / User** | own org + *assigned* projects | own org, deny if a different one is asked for | deny unless the project is assigned |
| **any role, no reach** | deny-all (`{"_id": {"$in": []}}`) | deny-all | deny-all |

### The gate and the query are different jobs

`ScopeService.is_client_scope_allowed` (reached through `PolicyService.authorize`)
answers **membership** — may this caller act in this org/project at all. It does
*not* bound rows. `build_scope_query` does that. A collection read by a
tenant-bound caller therefore passes the gate on the strength of having *some*
reach, and is then narrowed to exactly that reach by the query.

Getting this backwards in either direction has already caused a bug: denying
project-tier callers at the gate whenever no project was selected locked them
out of listing their own projects, while the older behaviour of letting the read
through *org*-filtered leaked same-org projects they were not assigned to. The
gate refuses only a caller with no reach at all.

Two invariants hold across every row:

1. **A selection only ever narrows.** No combination may widen the result past
   the caller's entitlement — selecting an unassigned organisation denies, it
   does not fall back to a consolidated view.
2. **Absence of a selection is not a licence to widen.** Only a genuinely
   global role consolidates; every other tier stays bounded by its assignment.

Pinned by `test_progressive_scope_narrowing.py` (the query half),
`test_scope_selection_seam.py` (the auth-layer half — that a home organisation
never reaches the query as a selection), and the `build_scope_query` cases in
`test_tenant_isolation.py`.

## Removed / forbidden

These were removed in the Week-1 consolidation and are blocked by a pre-commit hook
(`forbid-legacy-authz`):

- **`core.security.has_permission(permission)`** — a dependency that queried
  `db.roles` directly and swallowed errors into a superadmin bypass. Use
  `require_permission` or `PolicyService` instead.
- **`core.security.require_roles(*roles)`** — role-name gating that bypassed the
  permission model. Use permissions, not raw role names.
- **`_ensure_scope(...)`** (retrieval engine) — replaced by `PolicyService.authorize`
  in Hotfix #1.
- **Direct `db.roles.find(...)` permission checks** in routers.
- **Imports from the deleted `archive/` package.**

## Auditing

Every `PolicyService.authorize(...)` call emits a `policy.authorize` audit event
(`allow`/`deny` + reason) via `AuditEventService`. When you add a new gated endpoint,
authorizing through `PolicyService` gives you the audit trail for free — do not add
ad-hoc gates that skip it.
```
