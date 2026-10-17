---
status: accepted
date: 2026-09-15
---

# Super Admin authority attaches to the role name; only an explicit deactivation revokes it

Super Admin power is granted wherever `"superadmin"` appears in an account's normalised roles
(`ScopeService.is_superadmin`, `require_permission`, `PolicyService`). Since R-A9B (release tip
`93bbc30`) the principal builder drops a role reference whose role document has `is_active=False`,
but keeps a reference that has no role document at all. We keep that shape for the release cutover:
an explicit deactivation revokes Super Admin, while a missing or damaged role document cannot lock
out break-glass administration. The controls are therefore who holds the role, who can assign it,
and the deactivation flag, not the role document's permission list.

## Consequences

- A missing `superadmin` role document still grants. An audit checks the holder list and that the
  document exists and is not deactivated.
- Deactivating the `superadmin` document revokes every holder on their next request. Recovery needs
  a direct database write, so deactivation is the lockout lever and must be treated as one.
- Any stored role string that normalises to `superadmin` and has no document of its own (for
  example `super-admin`) also grants. Role-assignment validation is therefore a security boundary.
- The role document's permission list does not authorise a Super Admin. The startup seeder keeps it
  complete for display and for consumers that read permissions directly.

## Considered Options

- **Fail closed on a missing document.** Rejected for cutover: the seeder only updates an existing
  document (`update_one`, no upsert), so a missing document would lock out every platform operator.
- **Ignore deactivation (pure name trust, release `0249ded` and earlier).** Superseded by R-A9B: a
  soft-deleted system role kept granting by name, which is the defect F-A9A-1 described.
