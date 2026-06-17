import { api } from "./api";

// ---------------------------------------------------------------------------
// Correspondence SLA & time-bar API (Phase 4 / Module 2).
// Mirrors backend routers/sla.py — read-only deadline views derived from claims.
// ---------------------------------------------------------------------------

export type SlaKind = "response" | "time_bar";
export type SlaState = "ok" | "approaching" | "breached";

export interface SlaItem {
  claim_id: string;
  claim_ref?: string | null;
  title?: string | null;
  type?: string | null;
  status?: string | null;
  kind: SlaKind;
  due_date?: string | null;
  days_remaining: number;
  state: SlaState;
  organization_id?: string | null;
  project_id?: string | null;
  responsible_party_id?: string | null;
}

export async function getUpcoming(params?: {
  days?: number;
  project_id?: string;
}): Promise<SlaItem[]> {
  const { data } = await api.get("/sla/upcoming", { params });
  return Array.isArray(data) ? data : [];
}

export async function getBreached(params?: {
  project_id?: string;
}): Promise<SlaItem[]> {
  const { data } = await api.get("/sla/breached", { params });
  return Array.isArray(data) ? data : [];
}
