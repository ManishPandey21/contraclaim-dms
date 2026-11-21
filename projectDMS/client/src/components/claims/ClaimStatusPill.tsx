import React from "react";
import type { ClaimStatus } from "@/services/claims-api";
import { CLAIM_STATUS_BADGE, CLAIM_STATUS_LABEL } from "@/lib/claims-helpers";

/**
 * Claim status as a solid pill. Not built on the shared Badge: that component
 * always applies a variant's own text and background colours, and a second set
 * of colour classes on top of it resolves by stylesheet order, which is how
 * the register ended up with dark text on a dark fill.
 */
const ClaimStatusPill: React.FC<{ status: ClaimStatus; children?: React.ReactNode }> = ({
  status,
  children,
}) => (
  <span
    className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold ${
      CLAIM_STATUS_BADGE[status] || "bg-slate-600 text-white"
    }`}
  >
    {CLAIM_STATUS_LABEL[status] || status}
    {children}
  </span>
);

export default ClaimStatusPill;
