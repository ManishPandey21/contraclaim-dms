/**
 * Routes that are platform- or account-level rather than project-level.
 *
 * These must stay reachable without a project, otherwise a user with no
 * project selected could not reach the very pages used to manage
 * organisations, projects and their own account.
 *
 * Everything not listed here is treated as project-scoped and gated by
 * TenantContextGate, so a new page is protected by default rather than by
 * remembering to opt in.
 *
 * Kept in its own module so MainLayout exports only its component (the
 * react-refresh/only-export-components lint rule is an error at
 * --max-warnings=0).
 */
export const CONTEXT_FREE_ROUTES = [
  '/',
  '/security-terms',
  '/overview',
  '/organizations',
  '/projects',
  '/profile',
  '/users',
  '/permissions',
  '/settings',
  '/plan-settings',
  '/subscription-management',
  '/notifications',
  '/legal-words',
  '/billing',
  '/admin',
  '/observability',
];

export function isContextFreeRoute(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, '') || '/';
  return CONTEXT_FREE_ROUTES.some(
    (route) => path === route || path.startsWith(`${route}/`),
  );
}
