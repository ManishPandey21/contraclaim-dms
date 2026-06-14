import { API_BASE_URL } from "@/config/api";
import { authenticatedFetch } from "./http";

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
    return {
      "Content-Type": "application/json",
    };
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
    const res = await authenticatedFetch(url, { headers: this.authHeaders() });
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
    const res = await authenticatedFetch(`${this.base}/email/groups`, {
      method: "POST",
      headers: this.authHeaders(),
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Failed to create group: ${res.status}`);
    return res.json();
  }

  async update(id: string, payload: EmailGroupUpdate): Promise<EmailGroup> {
    const res = await authenticatedFetch(`${this.base}/email/groups/${id}`, {
      method: "PUT",
      headers: this.authHeaders(),
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Failed to update group: ${res.status}`);
    return res.json();
  }

  async remove(id: string): Promise<void> {
    const res = await authenticatedFetch(`${this.base}/email/groups/${id}`, {
      method: "DELETE",
      headers: this.authHeaders(),
    });
    if (!res.ok) throw new Error(`Failed to delete group: ${res.status}`);
  }

  async resolveEmails(id: string): Promise<string[]> {
    const res = await authenticatedFetch(`${this.base}/email/groups/${id}/resolve`, {
      method: "POST",
      headers: this.authHeaders(),
    });
    if (!res.ok) throw new Error(`Failed to resolve group: ${res.status}`);
    return res.json();
  }
}

export const emailGroupsApi = new EmailGroupsApi();
