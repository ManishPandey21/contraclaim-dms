# JG-7(c): custom roles and row visibility - audit (2026-11-18)

**Owner decision (2026-11-18):** support explicitly authorised custom organisation/project roles in `build_scope_query`; audit every permission, membership, tenant-scope and role-resolution path first; never relax row visibility globally; a custom role alone is never enough - the user must hold the required effective permission and explicit organisation/project access; production compatibility pending P-05.

**Status update 2026-11-19:** the four prerequisites (section 3) are implemented as SEC-1 to SEC-4 and the design in section 4 as `permission_scope_query`, opted in on `GET /api/claims` only; see `docs/SECURITY_REMEDIATION_2026-11-19.md`. Implemented and locally verified, not production-approved. The text below is the 2026-11-18 audit as written.

**Status (2026-11-18): audited, not implemented.** The audit found that the platform cannot yet tell *where* a custom role's permission was granted, so "permission P + membership of organisation B" does not prove P was granted in B. Implementing the scope change on today's resolver would widen access across organisations. Four prerequisites (section 3) need an owner decision first; the design in section 4 is ready once they are in place. Every statement below was read in the code at `82973e9` (file:line), the key ones re-checked by hand.

## 1. What happens today

- A user whose roles are only custom roles: `build_scope_query` (`core/security.py:635`) recognises built-in keys only (superadmin, superuser, orgadmin/orguser, projectadmin/projectuser, ContraClaim expert roles) and returns deny-all (`:748`). `authorize_scope` (`:609-613`) refuses with 403.
- On a scoped route with a project they are assigned to: `PolicyService.authorize` passes (permission, entitlement, `ScopeService.is_client_scope_allowed` - which is role-agnostic and accepts an assigned project), then the list query returns no rows (200, empty) and `get_case` returns 404 (`routers/arbitration_drafting.py:98`). On a project they are not assigned to, the scope check already refuses. Tests work around this by giving custom-role users a built-in key too (`I/test_claim_signals_mongo.py:96-107`).

## 2. Paths audited

| Path | Where | Finding |
|---|---|---|
| Principal building | `core/security.py:135-172, 246-342` | Custom role ids kept as given (`role_reference.normalize_role_key` only rewrites legacy built-in spellings). Deactivated role documents are dropped (`_without_revoked_roles`, `:110-132`). A role's `scope`, `organization_id`, `project_id` are never read. Only superadmin has organisation/projects cleared. |
| Effective permissions | `services/permission_service.py:1011-1174` (`user_has_permission`), `:690-749`, `:778-856`; `services/policy_service.py:58-81` | **Permissions are aggregated across all of a user's roles with no scope** (`:1095-1112`); the role's organisation/project is never compared with the request. The Redis cache holds the flat aggregate only. |
| Tenant scope | `services/scope_service.py:95-124`; `core/tenant_context.py:255-294` | Membership from the principal (`organization_id`, `organizations`, `projects`) and `organization_memberships` / `project_memberships`. Unassigned projects only for orgadmin/orguser. A project-only request does not check the project's organisation; entitlement needs an organisation (`entitlement_service.py:272`). |
| Row visibility | `build_scope_query`, 63 call sites (routers and services, listed in section 5) | No permission input: it cannot know which permission an endpoint requires. |
| Role creation | `routers/roles.py:146-171`; `services/role_service.py:448-520`, `:165-177` | **No subset check:** a non-superadmin may put on a role any permission outside `NON_DELEGABLE_PERMISSIONS` = {`*`, `system:admin`, `dms.admin`, `drafting.admin`} and the prefixes `platform.`, `billing.`, `subscription.`, `permissions:`, `roles:`, whether or not they hold it. The check compares strings; a permission given by its catalogue ObjectId is not matched by it (reported by the audit, resolution path `permission_service.py:722-741`; not reproduced here). A superadmin may create a role with no `organization_id`. |
| Role assignment | `routers/users.py:921-1037`; `core/security.py:751-792` | An orgadmin may assign a role with **no** `organization_id` (`users.py:1016` refuses only a role naming another organisation); a projectadmin's check never compares the role's organisation; a superadmin may assign an organisation-A role to a user of organisation B. |
| Disabled users | `core/security.py:172, 234, 473-476` | The worker path refuses a disabled account (`:234`); the access-token path does not, and routers use `get_current_user` (not `get_current_active_user`); disabling relies on clearing the user's sessions in Redis (`routers/users.py:526-527`). |

## 3. Prerequisites (owner decision needed before JG-7(c))

1. **Per-role, scoped permission evaluation.** A permission held through a custom role counts only within that role's `organization_id` (and `project_id` for a project role). Roles without an `organization_id` grant nothing outside a platform scope. Today's flat aggregate (and its cache) cannot answer this; it is a change to the permission service used by every route, so it needs its own ticket and tests.
2. **Role-creation subset check.** A non-superadmin may only put on a role permissions they hold themselves (in that organisation/project), resolved to catalogue names before the check, so an ObjectId cannot slip past the non-delegable list. Existing roles that would fail it need a production reading (P-05) before any enforcement on them.
3. **Assignment fences.** Refuse assigning a role with no `organization_id`, or one of another organisation, to an organisation-scoped user, for every non-superadmin actor and, for superadmin, unless the target user is in the role's organisation.
4. **Disabled users refused at the token path** (or confirmation that session revocation is the intended and only control).

## 4. Design for JG-7(c), once section 3 is in place

- `build_scope_query(current_user, ..., permission=None)`: built-in branches unchanged. A new branch applies only when `permission` is given and the user holds it **through a custom role whose organisation (and project, for a project role) is the one requested**, the user is an active member of that organisation or assigned to that project, and the role is active. It returns an explicit `{org_field: org, project_field: {$in: projects}}`, never `{}` and never a top-level `$or`.
- Opt-in per call site: callers pass the permission they gated on. Callers that do not pass one keep deny-all for custom-role users, so nothing widens by default. `routers/tasks.py` (overwrites org/project keys), `routers/dashboard.py` (replaces `$or`) and every `if scope:` truthiness use are fixed before they opt in.
- `authorize_scope`: same rule through the same helper.
- Real-MongoDB tests: custom role only (rows in its organisation/project, none elsewhere), built-in roles unchanged, mixed assignments (custom + built-in), cross-tenant (a role of A held by a member of B grants nothing in B), revoked and deactivated roles, disabled user, role without organisation, privilege escalation (a role carrying a permission its creator lacks is refused at creation), and every opted-in route in the authorisation sweep.
- Production compatibility (P-05): how many production users hold only custom roles, which permissions those roles carry, and whether any role lacks an `organization_id` - read before enabling.

## 5. `build_scope_query` and `authorize_scope` call sites

Routers: insurance (4), contract_appraisal (3), claim_bench (2), hindrances, evidence_registers (3), claims, key_dates (7), dashboard, ipc_bills (3), evidence_graph (8), deep_planning (3 + `authorize_scope`), arbitration_drafting (3), chronology, variations (3), sla, bank_guarantees (4), contract_master, tasks (2), projects (2), storage_settings (`authorize_scope`). Services: letter_drafting/service, contract_service, authorization_service (+ 4 `authorize_scope`), letter_service, report_service (2), conversation_service, document_service (`_actor_scope_filter`, canonical document visibility), ai_service, contract_scope_resolver (`authorize_scope`). Riskiest for a widened filter: `routers/projects.py` (global `require_permission` is its only gate), `routers/tasks.py`, `routers/dashboard.py`, and every `if scope:` truthiness use.
