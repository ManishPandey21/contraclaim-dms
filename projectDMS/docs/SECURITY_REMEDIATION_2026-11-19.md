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

## Review of 2026-11-19 (owner instructions and independent security review)

Owner instructions (second review) asked for the SEC-1 project boundary, production compatibility, adversarial SEC-2/3 testing, SEC-4 across tokens, WebSockets and Redis, the residual defects, and independent review. An independent reviewer (a separate agent given the code and the owner's properties, not this report) found three High and one Medium issue in the first implementation. Every finding below was reproduced by a failing test before it was fixed.

### Defects found and fixed

| Id | Severity | Found by | Defect | Fix | Regression test |
|---|---|---|---|---|---|
| DEF-1 | High | Owner question | `grant_applies` never compared a named project's organisation: an organisation role of A authorised a project of B when the request left the organisation out or named A with a project of B. | `PermissionService.resolve_request_scope` reads the project's organisation every time (never cached); a missing project or a contradictory pair resolves to an organisation no custom grant names. Used by every check, the cached path and `permission_scope_query`. | `test_an_organisation_role_never_reaches_a_project_of_another_organisation` (service, PolicyService, HTTP, both mismatch directions, unknown project, cached path); `test_a_cached_grant_never_crosses_the_project_boundary` (real Redis, re-parented project) |
| R-1 | High | Independent review | A custom role named like an unseeded built-in key (`legal_reviewer`, `contract_expert`) was treated as built-in, escaping SEC-1 scoping and the SEC-3 fence; the reserved-name check missed underscored keys. Introduced by this change (name matching added in SEC-1). | Built-in only by `is_system`, stored `scope: system` or a built-in `_id`; never by name. Every built-in key reserved in any spelling. | `test_r1_a_custom_role_named_like_a_built_in_key_is_still_a_custom_role`; `T/test_role_grants.py::test_a_name_never_makes_a_role_built_in` |
| R-2 | High | Independent review | Alias fan-out: a creator needed only one equivalent of a name, while holders pass every check the name satisfies (`projects:update` passes 23, including org-tier `dms.contract.applicability.manage`). | The creator must hold every check the name satisfies, in the role's scope. | `test_r2_an_alias_cannot_carry_permissions_its_creator_does_not_hold` |
| R-3 | High | Independent review | A custom project role counted for requests naming only its organisation, so its permissions reached the whole organisation (and could be delegated organisation-wide). | A project grant counts only for a request naming that project. | `test_r3_a_project_role_never_counts_at_organisation_level` |
| R-4 | Medium | Independent review | Permission-only gates (`require_permission`, no scope) counted a custom role in any organisation the user was listed in. | With nothing named, a custom grant counts only in the user's primary organisation (stored with the cache entry). | `test_r4_an_unscoped_check_counts_custom_roles_only_in_the_primary_organisation` |
| DEF-2 | Medium | This review | An open notifications WebSocket was never re-checked: disabling an account did not close it. | The socket re-checks the account on every message and at least every 30 s; closes 4401 (1013 if the store cannot be read). | `T/test_ws_notifications_auth.py::test_an_open_socket_closes_when_its_account_is_disabled` (both flags) |
| DEF-3 | Medium | This review | `PUT /api/users/{id}` silently dropped `organizations`: a user moved to another organisation stayed a member of the old one. Pre-existing. | `organizations` is applied when sent; a move that does not name the memberships kept is refused (422). | `test_revocation_and_membership_changes_reach_the_cache` |
| DEF-4 | Medium | This review | `organizations:read`, `orgs:view` and `reports:view` pass non-delegable billing/subscription checks through the alias contract, and were delegable. | A name is non-delegable when any check it satisfies is. | `test_no_spelling_of_a_privileged_permission_reaches_a_role` |
| L-1 | Low | Independent review | Second login route (`routers/users.py`) checked only `disabled`. | Shared active-account rule. | (token already refused; no new test) |
| L-2 | Low | Independent review | Development headers looked users up by string id only, so an ObjectId user's disabled flag was skipped. | Both forms looked up. | (dev-only path; no new test) |
| L-3 | Low | Independent review | A project role kept counting after its project became inactive. | Inactive project grants nothing. | `test_r3_...` (inactive project assertion) |
| RR-1 | Medium | Independent re-review | After R-4, a custom project role still passed permission-only gates (nothing named) across its primary organisation. | With nothing named, only organisation roles of the primary organisation count; a project role needs its project named. | `test_r3_...` (unscoped assertion); `T/test_role_grants.py` |
| RR-2 | Low | Independent re-review | Deactivating or deleting a project did not clear its members' cached grants (up to 1 h stale). | Both paths announce the change before the write and clear the members' cached grants after it, without signing them out (`complete_grant_change`). | `test_deactivating_a_project_clears_its_members_cached_grants` (real Redis) |

**Owner decision needed (DEF-4 root cause):** the alias declarations that make `organizations:read` / `reports:view` satisfy billing and subscription view checks. Changing them would change who sees billing pages; until decided, those names cannot be delegated.

### Residual defects investigated (item 6)

| Id | Classification | Finding |
|---|---|---|
| DEF-5 | Production data dependent | `GET /api/users/me` (422 "Invalid ObjectId format"), `GET /api/me` (401 "no longer active") and token refresh fail for users whose `_id` is not an ObjectId: `UserService._validate_user_id` accepts ObjectIds only. Seeded and API-created users get ObjectIds, so production is affected only if it holds string ids. P-05 now counts them (`users_with_a_non_objectid_id`). Not fixed; ticket for after the reading. |
| DEF-6 | Test-fixture (harness) | `/api/notifications*` 500 ("Event loop is closed") occurs only in the second test of a run: `dependencies.get_notification_service` keeps one service bound to the first database handle. Production runs one loop and one database, so it is not reached there. The earlier note that it was specific to service accounts was wrong. Not fixed; low. |
| Note | Pre-existing, Info | `POST /permissions/check` lets any authenticated user ask about any `user_id`; `get_effective_permission_names` also advertises the user's own `permissions` field, which no check reads. Not changed. |

### SEC-4 coverage, with and without Redis

`I/test_security_redis_mongo.py` (real Redis): access and refresh tokens refused after disabling (directly in the store, and through the users API, where re-enabling does not revive the ended sessions); Redis configured but unreachable, fail-closed (503, never 200) and fail-open (401 for a disabled account); no Redis (`is_active: false` refuses access and refresh); disabled service account and worker principal. WebSocket: connect refused (existing tests) and open sockets closed (DEF-2). CI now starts a disposable Redis (`redis:7.2-alpine`) for this module.

### Cache and concurrency

Real Redis: cached grants never cross the project boundary (including a project re-parented after caching); role edits, role revocation and membership moves clear the cache; concurrent valid, revoking and invalid assignments end consistent with the stored roles.

### Independent re-review

The reviewer re-read the fixed tree: findings 1 to 3 closed; finding 4 closed for organisations, with RR-1 (Medium) and RR-2 (Low) remaining, both now fixed above; no Critical or High hole introduced by the fixes. Its Info notes, carried as decisions or tickets: older built-in role documents stored under an ObjectId without `is_system` or `scope: system` are custom roles now and grant an organisation's users nothing (P-05 counts roles named like a built-in; audit before deployment); built-in roles are assigned without a delegation check (by design: built-in semantics preserved); `GET /api/claims` authorises before row filtering, so a holder of the permission only through a project role must select the project (functional, not a hole); `POST /permissions/check` lets any signed-in user query any user (pre-existing); membership rows written outside the application do not clear the cache (no application path writes them).

### Mutation testing (second pass)

32 mutants across SEC-1 to SEC-4, the boundary, the WebSocket re-check, DEF-3, DEF-4, R-1 to R-4 and RR-1/RR-2: 30 killed; 2 equivalent (an inactive project's marker can never match a resolved organisation; a project named without its organisation already fails the organisation comparison). The redundant line of the second was removed; the explicit inactive check is kept for clarity.

### Production compatibility (P-05)

P-05 `custom_role_compatibility.changes` classifies affected users, counts only and no identities: class (custom role of an organisation the user is not in; project role for a project not assigned; project role for a project of another organisation; role naming no organisation held by an organisation user; `is_active: false` not disabled), role type, users, organisations affected and users per organisation (no organisation named), the permission entries at stake, and the recommended re-grant for each class. Also: roles named like a built-in (now custom), users with a non-ObjectId id, custom roles carrying a name that passes a non-delegable check. **Recommendation:** read P-05 in production; for each non-zero class, an administrator re-grants the access properly scoped (the reading names the re-grant) before the code is deployed; nothing is migrated or re-granted automatically.

### JG-7(c) scope

Unchanged: opt-in on `GET /api/claims` only. One behaviour tightened by R-3: a custom project-role holder lists claims by selecting the project (without one, 403). No other list opts in.

### Legacy `Other` claims

Unchanged: no claim, notice decision or type is changed; P-03 reports the counts.

### Verdict per ticket (evidence-based; none production-approved)

| Ticket | Verdict |
|---|---|
| SEC-1 | Implemented and verified locally after DEF-1, R-1, R-3, R-4, RR-1, RR-2 and L-3 were fixed; independent re-review found no remaining Critical or High issue. Production enforcement depends on P-05. |
| SEC-2 | Implemented and verified after R-2 and DEF-4; DEF-4 root cause needs an owner decision. |
| SEC-3 | Implemented and verified; R-1 closed the built-in-name bypass of the fence; DEF-3 fixed. |
| SEC-4 | Implemented and verified with and without Redis; DEF-2 (open sockets) fixed. |
| JG-7(c) | Opt-in on the claims list only, verified; not expanded. |

## Production-dependent decisions (owner)

1. Read P-05 and P-03 in production (authorised read-only access).
2. For each non-zero P-05 count: accept the loss of access at deployment, or re-grant the roles correctly before it.
3. Authorised security review of SEC-1 to SEC-4 and JG-7(c).
4. Legacy `Other` claims: provisional or not.
