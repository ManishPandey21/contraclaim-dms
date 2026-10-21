
import React from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import Navbar from './Navbar';
import { ErrorBoundary } from '@/components/error-boundary/ErrorBoundary';
import { TenantProvider, useTenant } from '@/contexts/TenantContext';

const MainLayoutContent = () => {
  const location = useLocation();
  const { selectedOrganizationId, selectedProjectId } = useTenant();
  const tenantKey = `${selectedOrganizationId}:${selectedProjectId}`;
  // The Document Viewer sizes its PDF and metadata panes to the space left
  // under the Navbar, so on that route the shell is bounded to the viewport
  // and <main> hands its height down. <main> stays the scroller: on a short
  // window the viewer's own minimum height makes <main> scroll a little
  // instead of crushing the panes. Every other route keeps the page-scrolling
  // shell.
  const boundedShell = location.pathname.startsWith('/documentviewer/');
  return (
    <div className={`bg-docsumo-light flex ${boundedShell ? 'h-screen overflow-hidden' : 'min-h-screen'}`}>
      <Sidebar />
      <div className={`flex flex-col flex-1 overflow-hidden ${boundedShell ? 'min-h-0' : ''}`}>
        <Navbar />
        <main className={`flex-1 overflow-auto p-6 transition-all animate-fade-in ${boundedShell ? 'min-h-0' : ''}`}>
          {/*
            Section-level boundary: a crash in the routed page is contained to
            the content area so the Sidebar/Navbar shell stays usable. Keyed by
            pathname because React error boundaries do not auto-reset — without
            the key a crashed page would keep showing the fallback even after
            the user navigates elsewhere.
          */}
          <ErrorBoundary key={`${location.pathname}:${tenantKey}`}>
            <div key={tenantKey} className={boundedShell ? 'h-full min-h-0' : undefined}>
              <Outlet />
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
