
import React from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import Navbar from './Navbar';
import { ErrorBoundary } from '@/components/error-boundary/ErrorBoundary';

const MainLayout = () => {
  const location = useLocation();
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
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
};

export default MainLayout;
