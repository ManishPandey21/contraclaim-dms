import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { OPEN_AUTHENTICATED_ROUTES, ROUTE_PERMISSIONS } from "../rolePermissions";
import { PUBLIC_ROUTES as DECLARED_PUBLIC_ROUTES } from "../publicRoutes";

// Phase 0 — route inventory guard.
// Parses every <Route path="..."> declared in routes.tsx and asserts each
// protected path is covered by ROUTE_PERMISSIONS (exact or prefix) or is an
// explicitly public/open route. This makes the "unmapped route = dead route"
// failure mode (a Route that ProtectedRoute silently redirects to /overview)
// impossible to merge.

const here = dirname(fileURLToPath(import.meta.url));
const routesSource = readFileSync(
  resolve(here, "../../routes.tsx"),
  "utf8",
);

// Public/unauthenticated routes that intentionally have no permission mapping.
// Sourced from the config rather than restated here, so the declared public
// surface and the guard cannot drift apart.
const PUBLIC_ROUTES = new Set<string>(DECLARED_PUBLIC_ROUTES);
// Authenticated routes that are open to any signed-in user by design.
const OPEN_ROUTES = new Set<string>([...OPEN_AUTHENTICATED_ROUTES]);

function extractRoutePaths(source: string): string[] {
  const paths: string[] = [];
  // Non-greedy so we capture the <Route>'s own first `path=`, not a nested
  // wrapper's (e.g. a RoleGuard inside element={...}).
  const re = /<Route\b[^>]*?\bpath="([^"]+)"/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    paths.push(m[1]);
  }
  return paths;
}

function absolutize(path: string): string {
  if (path === "*" || path === "/") return path;
  return path.startsWith("/") ? path : `/${path}`;
}

function isCovered(path: string): boolean {
  const p = absolutize(path).replace(/\/+$/, "") || "/overview";
  if (PUBLIC_ROUTES.has(p) || OPEN_ROUTES.has(p)) return true;
  if (ROUTE_PERMISSIONS[p]) return true;
  // Prefix match (mirrors isRouteAllowedByPermission), so param/child routes
  // like /letters/:id/draft are covered by their base ("/letters").
  return Object.keys(ROUTE_PERMISSIONS).some(
    (base) => p === base || p.startsWith(base + "/"),
  );
}

describe("Route inventory guard (Phase 0)", () => {
  const routePaths = extractRoutePaths(routesSource);

  it("discovers the declared routes", () => {
    expect(routePaths.length).toBeGreaterThan(20);
    // sanity: known routes are present
    expect(routePaths).toContain("claims");
    expect(routePaths).toContain("contracts/appraisal");
  });

  it("declares the public blog routes as public and leaves them unmapped", () => {
    for (const path of [
      "/blog",
      "/blog/articles/:slug",
      "/blog/videos/:slug",
      "/blog/*",
    ]) {
      expect(routePaths).toContain(path);
      expect(PUBLIC_ROUTES.has(path)).toBe(true);
      // A permission mapping on a public route would be a contradiction.
      expect(ROUTE_PERMISSIONS[path]).toBeUndefined();
    }
  });

  it("keeps authenticated application routes out of the public set", () => {
    for (const path of ["/documents", "/contracts", "/claims", "/organizations"]) {
      expect(PUBLIC_ROUTES.has(path)).toBe(false);
    }
  });

  it("every <Route> path is permission-mapped, public, or open", () => {
    const unmapped = routePaths.filter((p) => !isCovered(p));
    expect(
      unmapped,
      `Unmapped routes (add to ROUTE_PERMISSIONS or mark public/open): ${unmapped.join(", ")}`,
    ).toEqual([]);
  });
});
