/**
 * Routes served without a session.
 *
 * These sit outside ProtectedRoute in routes.tsx and therefore carry no
 * permission mapping by design. Declaring them here rather than leaving the
 * knowledge inside a test keeps the public surface explicit and reviewable:
 * anything added to this list is a deliberate decision to serve a page to
 * anonymous visitors. The route inventory guard reads it directly, so a new
 * route that is neither listed here nor permission-mapped still fails the
 * build.
 *
 * `/blog*` is the public marketing blog. It renders bundled editorial content
 * only — it makes no API calls and reads no organisation, project or user
 * data.
 */
export const PUBLIC_ROUTES: readonly string[] = [
  "/",
  "/login",
  "/blog",
  "/blog/articles/:slug",
  "/blog/videos/:slug",
  "/blog/*",
  "*",
];

/** Exact public URLs allowed to opt into indexing. Keep this deliberately
 * narrower than PUBLIC_ROUTES: login, wildcards and unknown slugs fail closed. */
export const INDEXABLE_PUBLIC_ROUTES: readonly string[] = [
  "/",
  "/blog",
  "/blog/articles/why-construction-claims-fail-before-submission",
  "/blog/articles/seven-records-every-eot-claim-needs",
  "/blog/articles/excel-registers-versus-connected-contractual-records",
  "/blog/articles/how-to-build-defensible-project-chronology",
  "/blog/articles/what-ai-should-and-should-not-do-contractual-drafting",
];

export function isIndexablePublicPath(pathname: string): boolean {
  const path = pathname.split(/[?#]/, 1)[0] || "/";
  const normalized = path.length > 1 ? path.replace(/\/+$/, "") : path;
  return INDEXABLE_PUBLIC_ROUTES.includes(normalized);
}
