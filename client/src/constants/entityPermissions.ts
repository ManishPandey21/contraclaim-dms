export const ENTITY_PERMISSIONS = {
  organizations: {
    read: "organizations:read",
    create: "organizations:create",
    update: "organizations:update",
    delete: "organizations:delete",
  },
  projects: {
    read: "projects:read",
    create: "projects:create",
    update: "projects:update",
    delete: "projects:delete",
  },
} as const;

export type EntityPermissionKey =
  | typeof ENTITY_PERMISSIONS.organizations.read
  | typeof ENTITY_PERMISSIONS.organizations.create
  | typeof ENTITY_PERMISSIONS.organizations.update
  | typeof ENTITY_PERMISSIONS.organizations.delete
  | typeof ENTITY_PERMISSIONS.projects.read
  | typeof ENTITY_PERMISSIONS.projects.create
  | typeof ENTITY_PERMISSIONS.projects.update
  | typeof ENTITY_PERMISSIONS.projects.delete;
