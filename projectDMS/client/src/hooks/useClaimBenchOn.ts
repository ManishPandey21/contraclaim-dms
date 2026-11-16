import { useEffect, useState } from "react";

import { useOptionalTenant } from "@/contexts/TenantContext";
import { fetchClaimBenchAvailability } from "@/services/claim-bench-api";

/**
 * Whether the server has the Claim Bench on, for pages that do not use the
 * query client. False until the server says otherwise, and false if it cannot
 * be asked: those pages then behave as they did before the Bench existed.
 * Asked again whenever the navbar organisation changes, because the rollout is
 * decided per organisation.
 */
export function useClaimBenchOn(): boolean {
  const organizationId = useOptionalTenant()?.selectedOrganizationId ?? "";
  const [on, setOn] = useState(false);
  useEffect(() => {
    let active = true;
    setOn(false);
    fetchClaimBenchAvailability()
      .then((availability) => {
        if (active) setOn(Boolean(availability?.enabled));
      })
      .catch(() => {
        if (active) setOn(false);
      });
    return () => {
      active = false;
    };
  }, [organizationId]);
  return on;
}
