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

## Role lifecycle: a deleted role grants nothing

`RoleService.delete_role` is a soft delete (`is_active=False`, `deleted_at`,
`deleted_by`). The document stays: it is hidden from listings and still readable by
id (behind `roles:read` + `can_view_role`) for history and audit.

Readable is not effective. `PermissionService` resolves every `users.roles` entry
through `_load_role`, and a role whose `is_active` is `False` contributes **no
permission, no wildcard and no role name** to `user_has_permission`,
`get_user_permissions`, `get_effective_permission_names` or `check_resource_access`
(`role_is_active`; a document without the flag predates soft delete and is active).
System roles are referenced by their key (`users.roles: ["superadmin"]`) and
name-based decisions (superadmin bypass, scope, role manageability) read
`CurrentUser.roles`, so the principal itself drops references to inactive roles:
`get_current_user` and `resolve_stored_principal` both build it through
`_without_revoked_roles`. A bare key with no role document is kept, as before.

`delete_role` sets every holder's `user_jwt_min_iat` and then drops their cached
grant, like every other role mutation. A cached grant stores `computed_at`, stamped
before its roles were read. A hit is served only if it is strictly newer, in whole
seconds, than `user_jwt_min_iat`. That way a check that read the roles just before
a deletion cannot write back a grant that outlives it. There is no second lifecycle
flag and no reactivation API. `DataInitializer.initialize_roles` (reached
only through `SetupService`) rewrites `is_active` from `DEFAULT_ROLES`, so re-running
setup would reactivate a deleted system role.

Build cache keys with `permission_cache_key(user_id)` only. The key moved off
`user_perms:{id}` in R-A9B so entries computed before the fix are never read.

## Authority changes are announced before they are written (R-A9D, D4-B)

`user_has_permission` serves a cached grant for up to `PERMISSION_CACHE_TTL_SECONDS`
(one hour). Every mutation that changes someone's authority - `RoleService`
`update_role`, `delete_role`, `update_role_permissions`, `add_permission_to_role`,
`remove_permission_from_role`, and a `roles` change in `UserService.update_user` - uses
the three functions in `services/permission_service.py`:

1. **`begin_authority_change(user_ids)` before the write.** It sets
   `authz_change_pending:{id}` for every affected user (role holders, legacy spellings
   included). While that marker exists no cached grant is served and no new one is
   written. If the holders cannot be read, the store is configured but unreachable, or a
   marker cannot be written, it raises `AuthorityChangeUnavailableError` and **the
   mutation is not made**: the role routes answer 503 through `RoleServiceError`, the user
   route through the domain-error handler.
2. **The write.**
3. **`complete_authority_change(user_ids)` after it**: `user_jwt_min_iat`, then the cached
   grant, then the marker, in that order. If any step fails the marker stays. It lives
   `AUTHORITY_CHANGE_PENDING_TTL_SECONDS`, longer than any entry that could predate the
   change, so a failed invalidation costs cache bypass, never stale authority. A write
   that matched nothing calls `cancel_authority_change` instead.

A deployment with no runtime Redis has no permission cache, so there is nothing to
announce and the mutation proceeds. `test_authority_cache_invalidation_fail_closed.py`
pins every branch; do not add a role or user mutation that bypasses these three calls.

## System administration is nobody's alias (R-A9D, F-A9B-2)

`system:admin` ("Full system administration", non-delegable) gates the platform-wide
`/api/admin/legal-words`. It is declared as no canonical permission's legacy name, in the
backend `LEGACY_PERMISSION_ALIASES` and the client `PERMISSION_ALIASES` alike. Holding
`dms.admin`, `billing.plan.manage` or any `subscription.*` administration does not satisfy
it, and holding it does not satisfy them. It is reached only by holding it (only a Super
Admin can grant it), a `*` wildcard, or the Super Admin principal.
`test_system_admin_authority_contract.py` pins this at the resolver and at the route.

## SSO default role (R-A9D)

`OIDC_DEFAULT_ROLE` becomes the stored role of every newly provisioned SSO user, and Super
Admin authority is name-based (ADR 0001). `oidc_service.resolve_default_role` therefore
refuses provisioning unless the setting is a canonical key (no alias, case or spacing
variant), not `superadmin`/`superuser`, and names exactly one active, non-system-scoped
role document; the production config gate refuses the unsafe spellings before startup.

## Role documents are aligned to the release contract by an explicit operation (R-A9D)

`services/role_contract_alignment.py` adds to the `orgadmin` and `projectadmin`
documents the permissions this release's `DEFAULT_ROLES` gives them, and nothing else: no
removal, no user read or write, no other role, no creation, no reactivation, nothing for
an organisation-bound document. It is a cutover step, **not** a catalogued migration: it
must not run unreviewed on `migrate_database --apply`, and a catalogue entry would expire
the proven fresh-install and upgrade-path evidence. Run
`python -m rbac_backend.scripts.align_role_contract` (inspect) and then `--apply`, which
prints the before / expected / additions / after report and proves a second apply is a
no-op. It does not invalidate caches: it only adds authority, so a cached decision can be
briefly more restrictive, never more permissive.

## Role references: one resolver for tier, permissions and the audit

A `users.roles` entry is resolved by `core/role_reference.py::resolve_role_reference`
and nothing else. The principal (`_without_revoked_roles`, which decides the tier the
account carries) and `PermissionService._load_role` (which decides whose permissions
apply) both call it, so an account can never again have a role's tier without its
permissions, or the reverse. `scripts/system_role_audit.py` embeds a byte-identical copy
(pinned by `test_role_reference_single_definition.py`) and fails when the running image's
copy classifies any reference differently.

- A reference equal to a role document's `_id` resolves to that document.
- Otherwise the key the principal carries for it (`normalize_role_key`: `ROLE_ALIASES`,
  else the lowercased reference) is tried ONE hop: a legacy spelling such as
  `organization-admin` resolves to the `orgadmin` document and receives exactly its
  permissions, and `DocController` resolves to `doccontroller`. It resolves only if no
  other role document carries the reference as its exact name; otherwise it is ambiguous
  and grants nothing.
- A reference contributes to the name-based grants (`users:create`, `users:read`,
  superadmin `*`) exactly the key the principal carries. `permission_service`'s wider
  `_normalize_role_name` table applies to role document display names only.
- Role mutations reach every holder the resolver would give the role to: cache
  invalidation and holder counts match legacy spellings as well as the id. No role may
  be created or renamed so its name spells another role - a legacy spelling of a canonical
  key, or an existing role's key (400, every actor; re-sending an unchanged name is not a
  rename), because such a lookalike would make every holder of that spelling, in every
  organisation, ambiguous. Non-superadmins also may not use a `superadmin`/`superuser`
  key as a name.
- An alias of a system role (`super-admin`) never loads the system document. The
  principal still carries `superadmin` by name (ADR 0001); assignment stores the canonical
  id, and the audit fails the stored alias.
- A deactivated target, reached directly or through an alias, revokes the reference: no
  permission, no role name, no tier.
- Anything else grants nothing. There is no second hop, no lookup by name, and no link to
  permission aliases, which resolve separately (next section).

New writes never store a legacy spelling: `routers/users.py::_resolve_assigned_roles`
stores the resolved document's `_id`. The audit WARNs for a stored legacy spelling only
when authorization really resolves it to an active non-system canonical document.

## Legacy permission aliases translate a name; they never create authority

`LEGACY_PERMISSION_ALIASES` declares, per canonical permission, the legacy names
that used to gate the same capability; `LEGACY_LABEL_ALIASES` holds one-to-one older
spellings (`orgs:view` = `organizations:read`). `equivalent_permissions` is the
only function that reads them:

- A **canonical holder passes a legacy route gate** its alias names
  (`dms.task.manage` passes `require_permission("projects:update")`).
- A **stored legacy name passes each canonical check that declares it**
  (`projects:update` in a role passes `dms.project.manage`).
- **Nothing is transitive.** Canonicals sharing a legacy alias are not equivalent
  (`dms.task.manage` does **not** pass `dms.project.manage`), and legacy names
  sharing a canonical are not either (`projects:create` does not pass
  `projects:delete`).

So a legacy route dependency is coarse by design; the canonical
`PolicyService.authorize(...)` behind it is the precise gate. Do not pre-expand a
name before calling `user_has_permission` - that re-creates the second hop
(R-A9B: it let `billing.plan.manage` reach `dms.admin`).
`test_permission_alias_contract.py` holds the matrix over every shared alias.

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
