// Shared by PermissionsPage and its catalog test. Kept out of the page module so
// that module exports only components (react-refresh/only-export-components).
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
