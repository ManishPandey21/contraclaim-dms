import { api } from "./api";

/** One person a picker may offer: never an email or a role list (4B-11). */
export interface PickerUser {
  id: string;
  name: string;
}

export interface PickerUsersResponse {
  items: PickerUser[];
  /** More people match than were returned: ask the user to narrow the search. */
  truncated: boolean;
}

/**
 * Active members of one project who hold `permission` (C-21). The server reads
 * the project's organisation itself and applies the explicit-grant rule for
 * privileged permissions, so this list is what the action will accept.
 */
export async function listPickerUsers(params: {
  projectId: string;
  permission: string;
  q?: string;
  limit?: number;
}): Promise<PickerUsersResponse> {
  const { data } = await api.get<PickerUsersResponse>("/pickers/users", {
    params: {
      project_id: params.projectId,
      permission: params.permission,
      q: params.q?.trim() || undefined,
      limit: params.limit,
    },
  });
  return { items: data?.items ?? [], truncated: Boolean(data?.truncated) };
}
