
import React from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import Navbar from './Navbar';
import { ErrorBoundary } from '@/components/error-boundary/ErrorBoundary';
import { TenantProvider, useTenant } from '@/contexts/TenantContext';
import TenantContextGate from './TenantContextGate';

/**
 * Routes that are platform- or account-level rather than project-level. These
 * must stay reachable without a project, otherwise a user with no project
 * selected could not reach the very pages used to manage organisations,
 * projects and their own account.
 *
 * Everything not listed here is treated as project-scoped and gated, so a new
 * page is protected by default rather than by remembering to opt in.
 */
const CONTEXT_FREE_ROUTES = [
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

const MainLayoutContent = () => {
  const location = useLocation();
  const { selectedOrganizationId, selectedProjectId } = useTenant();
  const tenantKey = `${selectedOrganizationId}:${selectedProjectId}`;
  const contextFree = isContextFreeRoute(location.pathname);
  return (
    <div className="min-h-screen bg-docsumo-light flex">
      <Sidebar />
      <div className="flex flex-col flex-1 overflow-hidden">
        <Navbar />
        <main className="flex-1 overflow-auto p-6 transition-all animate-fade-in">
          {/*
            Section-level boundary: a crash in the routed page is contained to
            the content area so the Sidebar/Navbar shell stays usable. Keyed by
            pathname because React error boundaries do not auto-reset — without
            the key a crashed page would keep showing the fallback even after
            the user navigates elsewhere.
          */}
          <ErrorBoundary key={`${location.pathname}:${tenantKey}`}>
            {/*
              Keyed by tenantKey so a context change remounts the page rather
              than letting it re-render with the previous project's state.
            */}
            <div key={tenantKey}>
              {contextFree ? (
                <Outlet />
              ) : (
                <TenantContextGate>
                  <Outlet />
                </TenantContextGate>
              )}
            </div>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
};

const MainLayout = () => (
  <TenantProvider>
    <MainLayoutContent />
  </TenantProvider>
);

export default MainLayout;
