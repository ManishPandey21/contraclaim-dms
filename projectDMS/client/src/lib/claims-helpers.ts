import type { SlaItem, SlaState } from "@/services/sla-api";
import type { ClaimDTO } from "@/services/claims-api";

// Pure helpers shared by the claims register and claim detail views (Phase 2).

export interface ClaimSla {
  state: SlaState;
  days_remaining: number;
  kind: string;
}

const SEVERITY: Record<SlaState, number> = { breached: 2, approaching: 1, ok: 0 };

/**
 * Collapse SLA items to the single most-urgent entry per claim id. "breached"
 * outranks "approaching"; within the same state the nearer deadline wins.
 */
export function buildSlaStateMap(items: SlaItem[]): Record<string, ClaimSla> {
  const map: Record<string, ClaimSla> = {};
  for (const item of items || []) {
    const current = map[item.claim_id];
    const incoming: ClaimSla = {
      state: item.state,
      days_remaining: item.days_remaining,
      kind: item.kind,
    };
    if (
      !current ||
      SEVERITY[incoming.state] > SEVERITY[current.state] ||
      (SEVERITY[incoming.state] === SEVERITY[current.state] &&
        incoming.days_remaining < current.days_remaining)
    ) {
      map[item.claim_id] = incoming;
    }
  }
  return map;
}

/** Default title for a follow-up task raised from a claim. */
export function followUpTaskTitle(claim: Pick<ClaimDTO, "title" | "claim_ref">): string {
  const label = claim.claim_ref || claim.title || "claim";
  return `Follow up: ${label}`;
}
