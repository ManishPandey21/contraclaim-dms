import { describe, expect, it } from "vitest";

import {
  PERMISSION_CATALOG_PERMISSION_ITEMS,
  ROLE_ADMIN_PERMISSION_ITEMS,
} from "../PermissionsPage";

describe("PermissionsPage permission catalog", () => {
  it("exposes role and permission catalog permissions for assignment", () => {
    const ids = new Set([
      ...ROLE_ADMIN_PERMISSION_ITEMS.map((permission) => permission.id),
      ...PERMISSION_CATALOG_PERMISSION_ITEMS.map((permission) => permission.id),
    ]);

    expect(ids).toEqual(
      new Set([
        "roles:read",
        "roles:create",
        "roles:update",
        "roles:delete",
        "roles:assign",
        "permissions:read",
        "permissions:create",
        "permissions:update",
        "permissions:delete",
      ]),
    );
  });
});
