# Security remediation: SEC-1 to SEC-4 and JG-7(c) (2026-11-19)

**Owner direction (2026-11-19):** fix the four JG-7(c) security findings before custom roles reach row visibility; then implement an opt-in, permission-aware `build_scope_query`. No production role, historical claim or export behaviour is changed automatically. Standing restrictions unchanged: rollout off, skills unpublished, PR #114 draft, no production migration, deployment or merge, Phase 5 on hold.

**What each status means here**

| Status | Meaning |
|---|---|
| Implemented | Code and tests on `claude/zen-ptolemy-695liz` |
| Independently verified | Local real-MongoDB and unit suites green, mutants killed, CI run named in the register green except the known secret-scan findings |
| Production-approved | **None of these.** Needs P-05 read in production, authorised security review, staging acceptance (two consecutive deployed runs) and the owner's written acceptance |

Audit that found them: `docs/JG7C_CUSTOM_ROLE_SCOPE_AUDIT.md` (2026-11-18).

## Tickets, by severity

### SEC-2 (High): a role could carry permissions its creator could not delegate

- **Finding.** `role_service._ensure_permissions_assignable` refused only `*`, `system:admin`, `dms.admin`, `drafting.admin` and the `platform.` / `billing.` / `subscription.` / `permissions:` / `roles:` prefixes, compared as strings. An organisation admin could create a role holding any other permission, including the explicit-grant-only legal material (`dms.claim.strategy.read`, `dms.claim.determine`, `dms.claim.evidence.confirm_contract`, `dms.claim.signal.view`), and assign it to themselves or anyone in their organisation. A catalogue ObjectId was not resolved, so `dms.admin` given by id passed the string check. A project admin naming another project silently got a role in their first project.
- **Fix.** `services/role_service.py` `_resolve_permission_entry`, `_ensure_permissions_assignable`, `ensure_role_delegable`, `_validate_role_scope`; `core/permissions.py` `known_permission_name`. Every entry is resolved (catalogue id, label, name) and stored as the name. A non-Super-Admin may put on a role only a known permission (else 422) that is not non-delegable (judged on the entry and on its name, 403) and that they hold in the role's own organisation and project (an explicit-grant permission only by name, never through a wildcard; 403). Applies to create, edit, replace-permissions and add-permission. An organisation role must name an existing organisation and a project role an existing project of it (422). A project admin naming another project is refused (403). Built-in role keys (`orguser`, `projectuser`, `user`, the ContraClaim expert keys) are now reserved names.
- **Tests.** `I/test_security_remediation_mongo.py::test_a_role_carries_only_what_its_creator_may_delegate_there` (HTTP, step-up, real RBAC: valid role stored in the creator's organisation; explicit-grant permission 403; `dms.admin` by catalogue id 403; unknown 422; add and edit with a privileged permission 403 and nothing written; another project 403; Super Admin organisation role without an organisation refused); `T/test_role_management_phase6.py` (15, updated: reserved names now 403 for organisation admins).
- **Migration risk.** None stored is changed. Existing roles that would fail the check keep working and stay editable for fields other than permissions; their holders are bounded by SEC-1. P-05 reports how many exist.

### SEC-1 (High): custom-role permissions were pooled across organisations

- **Finding.** `PermissionService.user_has_permission` pooled every permission on every role a user held, with no reading of the role's `organization_id` or `project_id` (`permission_service.py:1095-1112` at `82973e9`). A role of organisation A held by a member of B granted its permissions in B; a project role granted in every project; the Redis cache held the pooled set.
- **Fix.** New `services/role_grants.py`. Each role is evaluated where it was granted: built-in roles (Super Admin, Super User, organisation/project admin/user, expert and reviewer keys, every seeded role, anything `is_system` or `scope: system`, and documents named like a built-in role) keep their meaning; a custom organisation role counts only for a member of that organisation and, when the request names one, only in it; a custom project role only for a user assigned to that project, which must belong to the role's organisation, and only in that project; a custom role naming neither grants an organisation's user nothing. `PermissionService.scoped_grants` is the one place role references are loaded; `user_has_permission`, `user_has_explicit_permission`, `get_user_permissions` and `get_effective_permission_names` read it, so what the client is told matches what the server allows. `PolicyService.has_permission` / `has_explicit_grant` / `authorize` pass the request's organisation and project. The cache key moved to `user_perms:v4:` and holds each role's names with their scope (a pooled v3 entry is never read). A membership change (`organization_id`, `organizations`, `projects`) or disabling is now an authority change that clears the cache (`services/user_service.py`). Custom-role names no longer grant `users:read` / `users:create` by name. `PermissionService._get_db` no longer caches the handle (a per-route service outlived the database it first saw).
- **Tests.** `I/test_security_remediation_mongo.py`: cross-organisation (a role of A held by a member of B grants nothing, and is not advertised; a member of both organisations gets each role only in its own), project roles (only in their project, only for assigned users, `project_memberships` honoured and revocation honoured), platform, revoked and mixed roles, explicit grants, built-in roles unchanged; `T/test_role_grants.py` (4, including every seeded role being built-in); `T/test_authority_cache_invalidation_fail_closed.py`, `T/test_role_soft_delete_contract.py`, `T/test_role_reference_single_definition.py` updated for the cache format and the single loader.
- **Migration risk.** Behaviour change at deployment, no data change: a user holding a custom role of an organisation they are not a member of, a project role for a project they are not on, or a role naming no organisation, loses those permissions. A cold permission cache on deployment (new key). **P-05 must be read first** (counts below).

### SEC-3 (High): roles could be assigned to users they did not fit

- **Finding.** An organisation admin could assign a role naming no organisation (`users.py:1016` refused only a role naming another organisation); a project admin's check never compared the role's organisation; a Super Admin could assign an organisation-A role to a user of B. Assignment did not check that the assigner could delegate the role's permissions.
- **Fix.** `routers/users.py` `_fence_added_roles`, run on create and on update for **roles being added** (a role already held is left alone, so no stored assignment is changed by an unrelated edit), for every caller, Super Admin included. For a custom role: none for a disabled account (409); a role naming no organisation only to a user bound to no organisation, by a Super Admin (403); an organisation role only to a user of that organisation (403); a project role only to a user assigned to it, in the user's organisation, while the project is active (403); and the assigner must be able to delegate every permission on it (SEC-2, 403). Built-in roles keep `_enforce_role_assignment_scope`, which now judges the projects the update sets, not only the stored ones.
- **Tests.** `I/test_security_remediation_mongo.py::test_a_role_must_fit_the_user_it_is_given_to` (Super Admin cross-organisation 403; organisation-less 403; project not assigned 403; privileged role by an admin who lacks the permission 403; nothing written; valid assignment 200 and effective; disabled account 409) and `::test_concurrent_valid_and_invalid_assignments` (8 concurrent requests: invalid ones all refused, valid ones applied, the foreign role never stored).
- **Migration risk.** None stored is changed. Other assignment routes: reviewer roles are granted only through reviewer assignments (4B-08, unchanged); SSO first-login provisioning (`services/oidc_service.py`) uses one configured default role, and a mismatched one grants nothing under SEC-1.

### SEC-4 (Medium): a disabled account kept authenticating

- **Finding.** The access-token path (`core/security.py principal_from_access_token`, behind every request and the notifications WebSocket) did not check `disabled`; disabling relied on clearing the user's Redis sessions, which a deployment without a runtime Redis does not have, and `is_active: false` was not honoured anywhere.
- **Fix.** `services/account_status.py` `account_is_active` (inactive when `disabled` is set or `is_active` is explicitly `false`), applied to the access-token path, development headers, background-worker principal resolution, password login, token refresh and SSO login. A disabled account stops on its next request (401). Disabling or deactivating also clears the account's sessions (`routers/users.py`) and its cached permissions.
- **Tests.** `I/test_security_remediation_mongo.py::test_a_disabled_account_stops_on_its_next_request` (`disabled` and `is_active: false`: the same token is refused; the background-worker principal is refused; a disabled service account stops too).
- **Migration risk.** Users stored with `is_active: false` but not `disabled` can no longer sign in. P-05 counts them.

### JG-7(c): custom-role row visibility (opt-in)

- **Built after SEC-1 to SEC-4 passed.** `core/security.py permission_scope_query`: for a principal with built-in roles, exactly `build_scope_query`; for one with custom roles only, rows only where it holds the list's permission **by name** (no wildcard) through a custom role that applies, in the request's or its own organisation: an organisation role gives that organisation's rows, project roles those projects' rows. A platform role, a role without the permission, a revoked role and a disabled account give none. The filter always names the organisation, is never `{}` and never a top-level `$or`. Selecting a project still needs an explicit assignment (the tenant gate). `build_scope_query` itself is unchanged and its 63 call sites keep deny-all for custom-role users.
- **Opted in:** `GET /api/claims` only.
- **Tests.** `I/test_security_remediation_mongo.py::test_custom_role_holders_see_claims_only_where_they_hold_the_permission` (organisation role: A's claims, not B's; project role: its project only; another project refused; a role without the permission 403; a role of B 403; built-in unchanged; revocation 403; disabled 401) and `::test_the_custom_role_filter_never_widens_and_always_names_the_organisation`.
- **Next call sites** need, each: a passed permission, a fix to any key-overwriting or `$or`-replacing caller (`routers/tasks.py`, `routers/dashboard.py`, `if scope:` truthiness), and a real-MongoDB test. Not done.

## Mutation testing

18 mutants of the new controls (scratchpad script, not committed), each run against `T/test_role_grants.py`, `T/test_role_management_phase6.py` and `I/test_security_remediation_mongo.py`: organisation membership, request organisation, request project, platform role, project membership, scope passed through PolicyService (SEC-1); held-permission check, non-delegable judged on the resolved name, unknown permissions (SEC-2); foreign organisation, organisation-less role, disabled target, delegation on assignment (SEC-3); token-path refusal, `is_active` (SEC-4); organisation bound, project bound, wildcard (JG-7(c)). 17 killed. One (PolicyService scope) survived at first and is killed by an added test. The wildcard mutant was equivalent (a `*` entry never matches a name); the redundant code was removed and a test pins the behaviour.

## P-05: what must be read in production first

`scripts/claim_arbitration_readonly_readings.py` P-05 now reports `custom_role_compatibility` (counts only, no names or ids): custom roles; custom roles naming no organisation or project; roles named like a built-in role; users with custom roles only; users holding a custom role of another organisation; users holding a project role without the project; organisation users holding a role naming no organisation; disabled users; users `is_active: false` but not disabled. Each non-zero count is a set of users whose access changes at deployment. P-03 now reports `other_type` for the legacy `Other` claim decision (below).

## Legacy `Other` claims (read-only assessment)

P-03 `other_type`: total `other` claims; legacy ones with no `type_confirmed` (they read as confirmed today); provisional; confirmed; legacy by status; legacy with frozen evidence; legacy claims in organisations with a verified `other` notice period (these show a period as verified against the contract although nobody chose the type); organisations affected. No claim is changed. The owner decides, after the reading, whether legacy `other` claims become provisional.

## Found while testing, not fixed (outside this change)

- `GET /api/notifications/unread-count` answers 500 for a `system_service` account and, under the real-MongoDB harness, after a database swap (its service caches a database handle). Not investigated further.
- `GET /api/users/me` answers 422 for the seeded test personas (response model validation). Not investigated further.

## Production-dependent decisions (owner)

1. Read P-05 and P-03 in production (authorised read-only access).
2. For each non-zero P-05 count: accept the loss of access at deployment, or re-grant the roles correctly before it.
3. Authorised security review of SEC-1 to SEC-4 and JG-7(c).
4. Legacy `Other` claims: provisional or not.
