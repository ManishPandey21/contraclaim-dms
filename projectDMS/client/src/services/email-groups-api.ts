import { API_BASE_URL } from "@/config/api";

export interface EmailGroup {
  _id: string;
  name: string;
  category?: string | null;
  emails: string[];
  organization_id?: string | null;
  project_id?: string | null;
}

export interface EmailGroupCreate {
  name: string;
  category?: string;
  emails?: string[];
  organization_id?: string;
  project_id?: string;
}

export interface EmailGroupUpdate {
  name?: string;
  category?: string;
  emails?: string[];
  organization_id?: string;
  project_id?: string;
}

class EmailGroupsApi {
  private base = API_BASE_URL;

  private authHeaders() {
    const token = localStorage.getItem("accessToken") || "";
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    const userId = localStorage.getItem("user_id");
    const userRoles = localStorage.getItem("user_roles");
    if (userId) headers["X-User-Id"] = userId;
    if (userRoles) headers["X-User-Role"] = userRoles;
    const orgId = localStorage.getItem("org_id") || "";
    const projId = localStorage.getItem("proj_id") || "";
    if (orgId) headers["X-Org-Id"] = orgId;
    if (projId) headers["X-Proj-Id"] = projId;
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return headers;
  }

  async list(params?: {
    organization_id?: string;
    project_id?: string;
    skip?: number;
    limit?: number;
    search?: string;
  }): Promise<EmailGroup[]> {
    const qs = new URLSearchParams();
    if (params?.organization_id)
      qs.append("organization_id", params.organization_id);
    if (params?.project_id) qs.append("project_id", params.project_id);
    if (typeof params?.skip === "number")
      qs.append("skip", String(params.skip));
    if (typeof params?.limit === "number")
      qs.append("limit", String(params.limit));
    if (params?.search) qs.append("search", params.search);

    const url = `${this.base}/email/groups${
      qs.toString() ? `?${qs.toString()}` : ""
    }`;
    const res = await fetch(url, { headers: this.authHeaders() });
    if (!res.ok) throw new Error(`Failed to fetch groups: ${res.status}`);
    // Backend returns a paginated response: { groups: EmailGroup[], total, page, limit, ... }
    const data = await res.json();
    const list: EmailGroup[] = Array.isArray(data)
      ? data
      : Array.isArray(data?.groups)
      ? data.groups
      : [];
    return list;
  }

  async create(payload: EmailGroupCreate): Promise<EmailGroup> {
    const res = await fetch(`${this.base}/email/groups`, {
      method: "POST",
      headers: this.authHeaders(),
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Failed to create group: ${res.status}`);
    return res.json();
  }

  async update(id: string, payload: EmailGroupUpdate): Promise<EmailGroup> {
    const res = await fetch(`${this.base}/email/groups/${id}`, {
      method: "PUT",
      headers: this.authHeaders(),
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Failed to update group: ${res.status}`);
    return res.json();
  }

  async remove(id: string): Promise<void> {
    const res = await fetch(`${this.base}/email/groups/${id}`, {
      method: "DELETE",
      headers: this.authHeaders(),
    });
    if (!res.ok) throw new Error(`Failed to delete group: ${res.status}`);
  }

  async resolveEmails(id: string): Promise<string[]> {
    const res = await fetch(`${this.base}/email/groups/${id}/resolve`, {
      method: "POST",
      headers: this.authHeaders(),
    });
    if (!res.ok) throw new Error(`Failed to resolve group: ${res.status}`);
    return res.json();
  }
}

export const emailGroupsApi = new EmailGroupsApi();
