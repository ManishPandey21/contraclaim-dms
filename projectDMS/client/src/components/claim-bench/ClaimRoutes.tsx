import React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useClaimBenchAvailability } from "@/hooks/useClaimBench";
import ClaimBenchPage from "@/pages/ClaimBenchPage";
import ClaimDetailPage from "@/pages/ClaimDetailPage";

/**
 * `/claims/:id`. Shows the Claim Bench where the server has it on for the
 * organisation, and the claim record page everywhere else. The record page
 * stays reachable at `/claims/:id/record` either way.
 */
export const ClaimRoute: React.FC = () => {
  const availability = useClaimBenchAvailability();
  if (availability.isLoading) {
    return (
      <div className="container mx-auto space-y-4 p-6">
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  return availability.data?.enabled ? <ClaimBenchPage /> : <ClaimDetailPage />;
};
