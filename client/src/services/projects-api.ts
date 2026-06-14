import { api } from "./api";

export interface Project {
  _id: string;
  name: string;
  organization_id: string;
  status?: "Active" | "Completed" | "On Hold";
  projectCode?: string;
  address?: string;
  city?: string;
  state?: string;
  pinCode?: string;
  startDate?: string;
  teamSize?: number;
}

function normalizeProject(p: any): Project {
  return {
    _id: p?._id ?? p?.id,
    name: p?.name ?? "",
    organization_id: p?.organization_id ?? p?.organizationId ?? "",
    status: p?.status,
    projectCode: p?.projectCode,
    address: p?.address,
    city: p?.city,
    state: p?.state,
    pinCode: p?.pinCode,
    startDate: p?.startDate,
    teamSize: p?.teamSize,
  };
}

export async function listProjects(params?: { organization_id?: string }) {
  const { data } = await api.get("/projects", { params });
  const raw = Array.isArray(data)
    ? data
    : (data as any)?.projects || (data as any)?.items || [];
  return (raw as any[]).map(normalizeProject);
}

export async function getProject(id: string) {
  const { data } = await api.get(`/projects/${id}`);
  return normalizeProject(data);
}

export async function createProject(payload: Omit<Project, "_id">) {
  const { data } = await api.post("/projects", payload);
  return normalizeProject(data);
}

export async function updateProject(id: string, payload: Partial<Project>) {
  const { data } = await api.put(`/projects/${id}`, payload);
  return normalizeProject(data);
}

export async function deleteProject(id: string) {
  const { data } = await api.delete(`/projects/${id}`);
  return data as { message?: string } | { detail?: string };
}
