import { api } from "./api";

// ---------------------------------------------------------------------------
// Tasks API client. Mirrors backend/rbac_backend/routers/tasks.py.
// The backend Task serialises its id as `_id` (response_model_by_alias); we
// normalise to `id` so callers always have a stable identifier.
// ---------------------------------------------------------------------------

export interface TaskComment {
  id: string;
  text: string;
  author_id?: string | null;
  author_name?: string | null;
  created_at: string;
}

export interface TaskDTO {
  id: string;
  title: string;
  description?: string | null;
  status?: string | null; // open | in_progress | done
  priority?: string | null; // low | normal | high
  assigned_to?: string | null;
  due_date?: string | null;
  document_id?: string | null;
  organization_id?: string | null;
  project_id?: string | null;
  comments: TaskComment[];
  created_at: string;
  updated_at?: string | null;
}

export interface CreateTaskPayload {
  title: string;
  description?: string;
  status?: string;
  priority?: string;
  assigned_to?: string;
  due_date?: string; // ISO 8601
  document_id?: string;
  organization_id?: string;
  project_id?: string;
}

function normalize(raw: any): TaskDTO {
  return {
    ...raw,
    id: raw?._id ?? raw?.id,
    comments: Array.isArray(raw?.comments) ? raw.comments : [],
  } as TaskDTO;
}

export async function getTasks(params?: {
  status_filter?: string;
  assigned_to?: string;
  project_id?: string;
  organization_id?: string;
}): Promise<TaskDTO[]> {
  const { data } = await api.get("/tasks", { params });
  return Array.isArray(data) ? data.map(normalize) : [];
}

export async function createTask(payload: CreateTaskPayload): Promise<TaskDTO> {
  const { data } = await api.post("/tasks", payload);
  return normalize(data);
}

export async function updateTask(
  id: string,
  payload: Partial<CreateTaskPayload>
): Promise<TaskDTO> {
  const { data } = await api.put(`/tasks/${id}`, payload);
  return normalize(data);
}

export async function deleteTask(id: string): Promise<void> {
  await api.delete(`/tasks/${id}`);
}

export async function addTaskComment(id: string, text: string): Promise<TaskDTO> {
  const { data } = await api.post(`/tasks/${id}/comments`, { text });
  return normalize(data);
}
