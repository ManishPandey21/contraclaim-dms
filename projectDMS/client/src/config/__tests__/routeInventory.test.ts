import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  OPEN_AUTHENTICATED_ROUTES,
  ROUTE_PERMISSIONS,
  isRouteAllowedByPermission,
} from "../rolePermissions";
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

// ---------------------------------------------------------------------------
// Route placement guard.
//
// The mapping guard above answers "is this path classified?". It does not
// answer "is this path actually behind the session boundary?" — a route can
// carry a permission mapping and still be declared outside `ProtectedRoute`,
// in which case the mapping is decoration and the route serves anonymously.
//
// This guard closes that gap structurally: it walks the <Route> tree in
// routes.tsx and asserts that every path not declared public is a
// `ProtectedRoute` itself or a descendant of one. A route is not public
// because it was omitted from the metadata; omission fails closed here.

type RouteNode = {
  path: string | null;
  /** The route's own opening tag, including its `element={...}` attribute. */
  openTag: string;
  children: RouteNode[];
};

/**
 * Parses the <Route> tree. Only <Route> nesting is tracked, which is all the
 * placement question needs: the end of an opening tag is the first `>` seen at
 * brace depth zero, so an `element={<X ... />}` attribute cannot end it early.
 */
function parseRouteTree(source: string): RouteNode[] {
  const roots: RouteNode[] = [];
  const stack: RouteNode[] = [];
  const token = /<Route\b|<\/Route>/g;
  let m: RegExpExecArray | null;

  while ((m = token.exec(source)) !== null) {
    if (m[0] === "</Route>") {
      stack.pop();
      continue;
    }
    let i = m.index + m[0].length;
    let braces = 0;
    for (; i < source.length; i += 1) {
      const c = source[i];
      if (c === "{") braces += 1;
      else if (c === "}") braces -= 1;
      else if (c === ">" && braces === 0) break;
    }
    const openTag = source.slice(m.index, i + 1);
    const selfClosing = source[i - 1] === "/";
    const pathMatch = /\bpath="([^"]+)"/.exec(openTag);
    const node: RouteNode = {
      path: pathMatch ? pathMatch[1] : null,
      openTag,
      children: [],
    };
    (stack.length ? stack[stack.length - 1].children : roots).push(node);
    if (!selfClosing) stack.push(node);
    token.lastIndex = i + 1;
  }

  return roots;
}

function collectPlacement(
  nodes: RouteNode[],
  inheritedProtection: boolean,
  out: Array<{ path: string; protected: boolean }>,
): void {
  for (const node of nodes) {
    const isProtected =
      inheritedProtection || node.openTag.includes("ProtectedRoute");
    if (node.path !== null) {
      out.push({ path: node.path, protected: isProtected });
    }
    collectPlacement(node.children, isProtected, out);
  }
}

describe("Route placement guard (session boundary)", () => {
  const placement: Array<{ path: string; protected: boolean }> = [];
  collectPlacement(parseRouteTree(routesSource), false, placement);

  it("parses the same route set as the mapping guard", () => {
    expect(placement.map((entry) => entry.path).sort()).toEqual(
      extractRoutePaths(routesSource).sort(),
    );
  });

  it("declares every non-public route inside ProtectedRoute", () => {
    const unprotected = placement
      .filter((entry) => !entry.protected)
      // Normalised the way publicRoutes.ts normalises, so "/" stays "/" rather
      // than collapsing to the empty string.
      .map((entry) => {
        const p = absolutize(entry.path);
        return p.length > 1 ? p.replace(/\/+$/, "") : p;
      })
      .filter((path) => !PUBLIC_ROUTES.has(path));
    expect(
      unprotected,
      `Routes served without a session (wrap in ProtectedRoute, or declare in PUBLIC_ROUTES): ${unprotected.join(", ")}`,
    ).toEqual([]);
  });

  it("keeps the Contract Master surfaces behind the session boundary", () => {
    for (const path of ["/contract-master/workspace", "/contract-master/prototype"]) {
      const entry = placement.find((candidate) => candidate.path === path);
      expect(entry, `${path} is not declared in routes.tsx`).toBeDefined();
      expect(entry!.protected, `${path} is declared outside ProtectedRoute`).toBe(true);
      expect(PUBLIC_ROUTES.has(path)).toBe(false);
      expect(isCovered(path), `${path} carries no permission mapping`).toBe(true);
    }
  });
});

describe("Contract Master route authorisation", () => {
  const CONTRACT_MASTER_PATHS = [
    "/contract-master/workspace",
    "/contract-master/prototype",
  ];

  // `can` is what useRBAC hands ProtectedRoute and RoleGuard; both call
  // isRouteAllowedByPermission with it. Denying here is what turns into
  // AccessDenied, so this is the authorisation decision itself, not a proxy.
  const deny = () => false;
  const grant = (held: string[]) => (perm: string) => held.includes(perm);

  it("denies an authenticated user holding no relevant permission", () => {
    for (const path of CONTRACT_MASTER_PATHS) {
      expect(isRouteAllowedByPermission(deny, path)).toBe(false);
      expect(isRouteAllowedByPermission(grant(["dms.dashboard.view"]), path)).toBe(false);
    }
  });

  it("admits a user holding the Contract Master audience permission", () => {
    for (const path of CONTRACT_MASTER_PATHS) {
      expect(isRouteAllowedByPermission(grant(["dms.contract.master.view"]), path)).toBe(true);
    }
  });

  it("requires the same audience as /contracts/master", () => {
    for (const path of CONTRACT_MASTER_PATHS) {
      expect(ROUTE_PERMISSIONS[path]).toEqual(ROUTE_PERMISSIONS["/contracts/master"]);
    }
  });

  it("is not open to every signed-in user", () => {
    for (const path of CONTRACT_MASTER_PATHS) {
      expect(OPEN_ROUTES.has(path)).toBe(false);
      expect(PUBLIC_ROUTES.has(path)).toBe(false);
    }
  });
});
