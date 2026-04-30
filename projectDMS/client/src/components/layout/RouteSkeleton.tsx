import React from "react";
import {
  Skeleton,
  SkeletonText,
  SkeletonCard,
  SkeletonTable,
} from "@/components/ui/skeleton";

/**
 * Generic page-level skeleton used as a Suspense fallback for lazy routes.
 * Designed to resemble a typical page with:
 * - Header (title + actions)
 * - Toolbar (search/filters)
 * - Main content (table + side cards)
 */
const RouteSkeleton: React.FC = () => {
  return (
    <div className="p-4 md:p-6">
      {/* Page header */}
      <div className="mb-6">
        <div className="flex items-center justify-between gap-4">
          <div className="space-y-2">
            <Skeleton className="h-7 w-48" />
            <Skeleton className="h-4 w-64" />
          </div>
          <div className="hidden md:flex items-center gap-2">
            <Skeleton className="h-9 w-24" />
            <Skeleton className="h-9 w-28" />
            <Skeleton className="h-9 w-24" />
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="mb-6 grid grid-cols-1 md:grid-cols-3 gap-3">
        <Skeleton className="h-10 w-full md:col-span-2" />
        <div className="flex items-center gap-2">
          <Skeleton className="h-10 w-full" />
        </div>
      </div>

      {/* Content area: table + sidebar cards */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <SkeletonTable rows={6} cols={5} />
        </div>
        <div className="space-y-4">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>

      {/* Mobile actions mimic */}
      <div className="mt-6 md:hidden space-y-3">
        <SkeletonText lines={2} />
        <div className="flex gap-2">
          <Skeleton className="h-9 w-24" />
          <Skeleton className="h-9 w-24" />
        </div>
      </div>
    </div>
  );
};

export default RouteSkeleton;
