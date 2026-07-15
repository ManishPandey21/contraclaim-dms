import { describe, it, expect } from "vitest";
import {
  expandPermissionSet,
  getRouteAccessDescriptor,
  isRouteAllowedByPermission,
  OPEN_AUTHENTICATED_ROUTES,
  ROUTE_PERMISSIONS,
} from "../rolePermissions";

// Phase 1 — SideBar route & permission alignment.
// Guards the "unmapped route = dead link" regression: because ProtectedRoute is
// deny-by-default on ROUTE_PERMISSIONS, any SideBar path without a mapping is
// silently redirected to /overview.

const canFor = (granted: string[]) => (p: string) => granted.includes(p);

// Every path the SideBar renders as a link (see components/layout/Sidebar.tsx).
const SIDEBAR_PATHS = [
  "/overview",
  "/dashboard",
  "/register",
  "/organizations",
  "/projects",
  "/parties",
  "/email-groups",
  "/upload",
  "/documents",
  "/documentsearch",
  "/letters",
  "/letter-quality",
  "/letter-templates",
  "/letter-templates/new/edit",
  "/contracts/upload",
  "/contracts/clauses",
  "/contracts/search",
  "/contracts/qa",
  "/contracts/viewer",
  "/contracts/appraisal",
  "/contracts/timeline",
  "/chronology",
  "/arbitration/cases",
  "/arbitration/drafts",
  "/arbitration/claim",
  "/arbitration/defence",
  "/arbitration/rejoinder",
  "/arbitration/counterclaim",
  "/claims",
  "/sla",
  "/key-dates",
  "/contracts/master",
  "/variations",
  "/bank-guarantees",
  "/ipc-bills",
  "/tasks",
  "/concerns",
  "/retrieval-console",
  "/folders",
  "/reports",
  "/legal-words",
  "/notifications",
  "/observability",
  "/health",
  "/users",
  "/permissions",
  "/plan-settings",
  "/admin/billing-catalog",
  "/admin/legal-words",
  "/subscription-management",
  "/settings",
];

const OPEN_ROUTES = new Set<string>([...OPEN_AUTHENTICATED_ROUTES]);

function hasMapping(path: string): boolean {
  const normalized = path.replace(/\/+$/, "");
  if (OPEN_ROUTES.has(normalized)) return true;
  if (ROUTE_PERMISSIONS[normalized]) return true;
  return Object.keys(ROUTE_PERMISSIONS).some(
    (base) => normalized === base || normalized.startsWith(base + "/"),
  );
}

describe("SideBar route ↔ permission parity (Phase 1)", () => {
  it("every SideBar path resolves to a permission mapping (no dead links)", () => {
    for (const path of SIDEBAR_PATHS) {
      expect(hasMapping(path), `${path} has no ROUTE_PERMISSIONS mapping`).toBe(
        true,
      );
    }
  });

  it("Claims, SLA, Contract Appraisal and Tasks are reachable for a document viewer", () => {
    const can = canFor(["dms.document.view"]);
    for (const path of ["/claims", "/sla", "/contracts/appraisal", "/tasks"]) {
      expect(
        isRouteAllowedByPermission(can, path),
        `${path} should be allowed for a document viewer`,
      ).toBe(true);
    }
  });

  it("Claims and SLA were the regression — they used to have no mapping", () => {
    // A non-document grant must not unlock them (proves the gate still bites).
    const can = canFor(["reports:view"]);
    expect(isRouteAllowedByPermission(can, "/claims")).toBe(false);
    expect(isRouteAllowedByPermission(can, "/sla")).toBe(false);
  });

  it("Contract Appraisal honours its own permission, not just the /contracts prefix", () => {
    // A user with ONLY appraisal-view (and not document-view) must still pass —
    // under the old prefix-only behaviour they would have been denied.
    const can = canFor(["dms.contract.appraisal.view"]);
    expect(isRouteAllowedByPermission(can, "/contracts/appraisal")).toBe(true);
  });

  it("Contract Timeline honours its graph permission, not just the /contracts prefix", () => {
    const can = canFor(["dms.evidence_graph.view"]);
    expect(isRouteAllowedByPermission(can, "/contracts/timeline")).toBe(true);
  });

  it("Chronology Builder honours its chronology permission", () => {
    const can = canFor(["dms.chronology.view"]);
    expect(isRouteAllowedByPermission(can, "/chronology")).toBe(true);
  });

  it("Subscription management stays behind a billing permission", () => {
    expect(
      isRouteAllowedByPermission(canFor(["dms.document.view"]), "/subscription-management"),
    ).toBe(false);
    expect(
      isRouteAllowedByPermission(canFor(["billing.plan.view"]), "/subscription-management"),
    ).toBe(true);
    expect(
      isRouteAllowedByPermission(
        canFor(["subscription.entitlement.manage"]),
        "/subscription-management",
      ),
    ).toBe(true);
  });

  it("Permissions page accepts role or permission catalog read access", () => {
    expect(isRouteAllowedByPermission(canFor(["roles:read"]), "/permissions")).toBe(true);
    expect(isRouteAllowedByPermission(canFor(["permissions:read"]), "/permissions")).toBe(true);
  });

  it("Unmapped routes are denied with a useful descriptor", () => {
    const descriptor = getRouteAccessDescriptor("/future-admin-screen");
    expect(descriptor.isMapped).toBe(false);
    expect(descriptor.requiredAnyPermissions).toEqual([]);
    expect(isRouteAllowedByPermission(canFor(["system:admin"]), "/future-admin-screen")).toBe(false);
  });

  it("Expands backend permission aliases without document-permission shortcuts", () => {
    const expanded = expandPermissionSet([
      "reports:view",
      "orgs:view",
      "projects:edit",
    ]);
    expect(expanded.has("dms.report.view")).toBe(true);
    expect(expanded.has("subscription.usage.view")).toBe(true);
    expect(expanded.has("organizations:read")).toBe(true);
    expect(expanded.has("projects:update")).toBe(true);
    expect(expanded.has("billing.plan.manage")).toBe(false);
    expect(expanded.has("dms.document.view")).toBe(false);
  });

  it("Legal words are open to signed-in users while admin stays gated", () => {
    expect(isRouteAllowedByPermission(canFor([]), "/legal-words")).toBe(true);
    expect(isRouteAllowedByPermission(canFor(["dms.document.view"]), "/admin/legal-words")).toBe(false);
    expect(isRouteAllowedByPermission(canFor(["system:admin"]), "/admin/legal-words")).toBe(true);
  });
});
