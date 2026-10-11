import { api } from "./api";

/**
 * One Programme Milestone (`programme_milestones`, evidence-graph register API).
 * The server holds record reads to the navbar selection (CL-3B): 400
 * `selection_required` with nothing selected, 403 `context_forbidden` for a
 * milestone of another project.
 */
export interface ProgrammeMilestoneDetailDTO {
  id: string;
  organization_id: string;
  project_id: string;
  milestone_ref: string;
  title: string;
  description: string | null;
  milestone_type: string;
  status: string;
  planned_date: string | null;
  forecast_date: string | null;
  actual_date: string | null;
  package: string | null;
  discipline: string | null;
  location: string | null;
}

export async function getProgrammeMilestone(id: string): Promise<ProgrammeMilestoneDetailDTO> {
  const { data } = await api.get(`/programme-milestones/${encodeURIComponent(id)}`);
  return {
    id: String(data?._id ?? data?.id ?? id),
    organization_id: String(data?.organization_id ?? ""),
    project_id: String(data?.project_id ?? ""),
    milestone_ref: String(data?.milestone_ref ?? ""),
    title: String(data?.title ?? ""),
    description: data?.description ?? null,
    milestone_type: String(data?.milestone_type ?? ""),
    status: String(data?.status ?? ""),
    planned_date: data?.planned_date ?? null,
    forecast_date: data?.forecast_date ?? null,
    actual_date: data?.actual_date ?? null,
    package: data?.package ?? null,
    discipline: data?.discipline ?? null,
    location: data?.location ?? null,
  };
}
