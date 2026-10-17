/**
 * Permission items the Permissions page offers for role administration.
 *
 * These live outside `PermissionsPage.tsx` because a module that exports a
 * component must export nothing else: `eslint-plugin-react-refresh` warns on a
 * mixed module (Fast Refresh cannot tell whether to remount), and the lint
 * script runs with `--max-warnings=0`, so the `frontend-checks` job fails on it.
 *
 * The ids are the wire names the API expects, not the `dms.*` canonical
 * permission names - this catalogue drives the assignment UI only. Its contents
 * are pinned by `__tests__/PermissionsPage.permission-catalog.test.ts`.
 */

export const ROLE_ADMIN_PERMISSION_ITEMS = [
  { id: "roles:read", name: "View Roles" },
  { id: "roles:create", name: "Create Roles" },
  { id: "roles:update", name: "Edit Roles" },
  { id: "roles:delete", name: "Delete Roles" },
  { id: "roles:assign", name: "Assign/Reset Roles" },
] as const;

export const PERMISSION_CATALOG_PERMISSION_ITEMS = [
  { id: "permissions:read", name: "View Permission Catalog" },
  { id: "permissions:create", name: "Create Permissions" },
  { id: "permissions:update", name: "Edit Permissions" },
  { id: "permissions:delete", name: "Delete Permissions" },
] as const;
