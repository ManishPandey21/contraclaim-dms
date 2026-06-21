import { api } from "./api";

// Observability (RAG/retrieval run logs + analytics). Mirrors
// routers/retrieval_engine.py (/api/v1/observability/*). Superadmins may query
// unscoped; others must pass org_id + project_id.

export interface RagRunLog {
  run_id?: string;
  run_type?: string;
  strategy?: string;
  query?: string | null;
  latency_ms?: number | null;
  created_at?: string | null;
  error?: string | null;
  org_id?: string | null;
  project_id?: string | null;
  breakdown_ms?: Record<string, number>;
  extra?: { counts?: Record<string, number> };
  [k: string]: unknown;
}

export async function getObservabilityLogs(params?: {
  org_id?: string;
  project_id?: string;
  run_type?: string;
  from?: string;
  to?: string;
}): Promise<RagRunLog[]> {
  const { data } = await api.get("/v1/observability/logs", { params });
  return Array.isArray(data) ? (data as RagRunLog[]) : [];
}

export interface AnalyticsGroup {
  total_runs?: number;
  error_counts?: Record<string, number>;
  top_queries?: Array<{ query: string; run_type?: string }>;
  [k: string]: unknown;
}

export async function getObservabilityAnalytics(payload: {
  org_id?: string;
  project_id?: string;
  window?: number;
}): Promise<AnalyticsGroup> {
  const { data } = await api.post("/v1/observability/analytics", payload);
  return data as AnalyticsGroup;
}
