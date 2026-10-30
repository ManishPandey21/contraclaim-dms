import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { getRouteAccessDescriptor, isRouteAllowedByPermission } from "../rolePermissions";

const rbac = vi.hoisted(() => ({ granted: new Set<string>(), loading: false }));
vi.mock("@/hooks/useRBAC", () => ({
  default: () => ({ can: (permission: string) => rbac.granted.has(permission), loading: rbac.loading }),
}));

import RoleGuard from "@/components/auth/RoleGuard";

const canFor = (granted: string[]) => (permission: string) => granted.includes(permission);

describe("Hindrance & Constraint Register route guard", () => {
  it("maps the register and its detail route to the register's own permission only", () => {
    expect(getRouteAccessDescriptor("/hindrances").requiredAnyPermissions).toEqual(["dms.hindrance.view"]);
    expect(getRouteAccessDescriptor("/hindrances/abc-123").matchedPath).toBe("/hindrances");
  });

  it("does not open for document or evidence-graph access alone", () => {
    for (const granted of [["dms.document.view"], ["dms.evidence_graph.view", "dms.evidence_graph.manage"], ["dms.keydate.view"]]) {
      expect(isRouteAllowedByPermission(canFor(granted), "/hindrances"), granted.join(",")).toBe(false);
      expect(isRouteAllowedByPermission(canFor(granted), "/hindrances/h-1"), granted.join(",")).toBe(false);
    }
    expect(isRouteAllowedByPermission(canFor(["dms.hindrance.view"]), "/hindrances/h-1")).toBe(true);
  });

  it("refuses a direct URL independently of the sidebar", () => {
    rbac.granted = new Set(["dms.document.view"]);
    render(
      <MemoryRouter initialEntries={["/hindrances/h-1"]}>
        <RoleGuard path="/hindrances"><p>Register content</p></RoleGuard>
      </MemoryRouter>,
    );
    expect(screen.queryByText("Register content")).not.toBeInTheDocument();

    rbac.granted = new Set(["dms.hindrance.view"]);
    render(
      <MemoryRouter initialEntries={["/hindrances/h-1"]}>
        <RoleGuard path="/hindrances"><p>Register content</p></RoleGuard>
      </MemoryRouter>,
    );
    expect(screen.getByText("Register content")).toBeInTheDocument();
  });
});
