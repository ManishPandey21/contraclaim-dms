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
