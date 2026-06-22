import { api } from "./api";

// IPC category masters (advance + deduction type catalogs).
// Mirrors routers/ipc_categories.py.

export type IPCCategoryKind = "advance" | "deduction";

export interface IPCCategory {
  id: string;
  kind: IPCCategoryKind;
  code: string;
  name: string;
  description?: string | null;
  active: boolean;
  sort_order: number;
  organization_id?: string | null;
  project_id?: string | null; // null = org-wide; set = project override/addition
  is_default: boolean;
}

export interface IPCCategoryPayload {
  kind: IPCCategoryKind;
  code: string;
  name: string;
  description?: string;
  active?: boolean;
  sort_order?: number;
  project_id?: string | null;
}

const norm = (raw: any): IPCCategory => ({
  ...raw,
  id: raw?._id ?? raw?.id,
  active: raw?.active ?? true,
  sort_order: raw?.sort_order ?? 0,
  is_default: raw?.is_default ?? false,
});

// Merged catalog (org-wide overlaid by project overrides) for the editor.
export async function getIPCCategories(params?: {
  project_id?: string;
  kind?: IPCCategoryKind;
}): Promise<IPCCategory[]> {
  const { data } = await api.get("/ipc-categories", { params });
  return Array.isArray(data) ? data.map(norm) : [];
}

// Raw entries (org + project, incl. inactive) for the management dialog.
export async function getIPCCategoriesManage(params?: { project_id?: string }): Promise<IPCCategory[]> {
  const { data } = await api.get("/ipc-categories/manage", { params });
  return Array.isArray(data) ? data.map(norm) : [];
}

export async function createIPCCategory(payload: IPCCategoryPayload): Promise<IPCCategory> {
  const { data } = await api.post("/ipc-categories", payload);
  return norm(data);
}

export async function updateIPCCategory(
  id: string,
  payload: Partial<Pick<IPCCategoryPayload, "name" | "description" | "active" | "sort_order">>,
): Promise<IPCCategory> {
  const { data } = await api.put(`/ipc-categories/${id}`, payload);
  return norm(data);
}

export async function deleteIPCCategory(id: string): Promise<void> {
  await api.delete(`/ipc-categories/${id}`);
}
