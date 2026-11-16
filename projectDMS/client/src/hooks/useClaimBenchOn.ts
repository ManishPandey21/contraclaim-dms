import { useEffect, useState } from "react";

import { fetchClaimBenchAvailability } from "@/services/claim-bench-api";

/**
 * Whether the server has the Claim Bench on, for pages that do not use the
 * query client. False until the server says otherwise, and false if it cannot
 * be asked: those pages then behave as they did before the Bench existed.
 */
export function useClaimBenchOn(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let active = true;
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
  }, []);
  return on;
}
