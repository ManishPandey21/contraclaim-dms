import {
  Party,
  Representative,
  RepresentativeLevel,
  RepresentativeCreateInput,
  RepresentativeUpdateInput,
  Concern,
  ApiError,
  Project,
  UserProfile,
  PartyCreateInput,
  PartyUpdateInput,
  ConcernCreateInput,
  ConcernUpdateInput,
  NotificationListResponse,
  NotificationPreference,
  NotificationPreferenceUpdate,
  ProjectNotificationSubscription,
  PartyListResponse,
} from "../types/api";

import axios from "axios";
import { API_BASE_URL, joinApiUrl } from "../config/api";
import {
  ensureValidToken,
  refreshToken,
  redirectToLoginAfterSessionExpiry,
} from "./auth";
import { authenticatedFetch } from "./http";

// Additional types for the enhanced API
export interface Organization {
  _id: string;
  name: string;
  panNumber?: string;
  gstNumber?: string;
  address?: string;
  city?: string;
  state?: string;
  pinCode?: string;
  adminName?: string;
  adminEmail?: string;
  adminContact?: string;
  billingEnabled?: boolean;
}

export interface User {
  // Backend returns "id" in /api/users response model; keep both for compatibility
  id?: string;
  _id?: string;
  username: string;
  first_name?: string;
  last_name?: string;
  email: string;
  roles: string[];
  organization_id?: string;
  projects?: string[];
  disabled?: boolean;
  is_superuser?: boolean;
  profile_photo_url?: string;
  // New display fields provided by backend for convenience
  organization_name?: string | null;
  project_names?: string[];
  preferences?: UserPreferences;
}

export interface UserPreferences {
  emailNotifications?: boolean;
  sharingAlerts?: boolean;
  theme?: string;
  language?: string;
}

export interface Document {
  _id: string;
  organization_id: string;
  project_id: string;
  filename: string;
  filepath_local: string;
  filepath_s3: string;
  presigned_url?: string;
  filetype: string;
  filesize: number;
  uploadType: "incoming" | "outgoing";
  letterNo?: string;
  date: string;
  subject: string;
  from_?: string;
  to?: string;
  tags?: string[];
  subTags?: string[];
  status: string;
  ocrEnabled?: boolean;
  compressionEnabled?: boolean;
  createdBy: string;
  createdAt: string;
  updatedAt?: string;
  references?: DocumentReference[];
  enclosures?: Enclosure[];
  processing_status?: string | null;
  processing_job_id?: string | null;
  processing_metadata?: Record<string, any> | null;
  processing_error?: Record<string, any> | null;
  processed_path?: string | null;
  metadata_source?: string | null;
  processed_at?: string | null;
  metadata?: Record<string, any> | null;
  summary?: string | null;
  keywords?: string[];
  additional_keywords?: string[];
  contractual_clauses?: string[];
  key_reply_points?: string[];
  asset_type?: string | null;
  location?: string | null;
  specific_area?: string | null;
  chainage_from?: string | null;
  chainage_to?: string | null;
  work_type?: string | null;
  issue_nature?: string | null;
  claim_category?: string | null;
  alleged_responsibility?: string | null;
  priority?: string | null;
  linked_event_suggested?: string | null;
  reference_chain?: string | null;
  extracted_tags?: string[];
  extracted_subTags?: string[];
}

export interface DocumentProcessingJobStatus {
  _id: string;
  document_id: string;
  status: string;
  stage?: string | null;
  attempts: number;
  max_attempts: number;
  error?: Record<string, any> | null;
  metadata?: Record<string, any> | null;
  created_at: string;
  queued_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
}

export interface DocumentProcessingResult {
  filename: string;
  success: boolean;
  document_id?: string;
  error?: string;
  row_number?: number;
  processing_time?: number;
  metadata_extracted?: boolean;
  ocr_completed?: boolean;
  embeddings_created?: number;
  status?: string;
  job_id?: string;
  message?: string;
}

export interface BulkUploadResponse {
  job_id: string;
  message: string;
  total_files: number;
  status: string;
  created_at: string;
}

export interface BulkUploadStatus {
  job_id: string;
  total_files: number;
  processed_files: number;
  successful_uploads: number;
  failed_uploads: number;
  status: string;
  created_at: string;
  updated_at?: string;
  completed_at?: string;
  error_message?: string;
  progress_percentage?: number;
  estimated_completion?: string;
  processing_rate?: number;
  results: DocumentProcessingResult[];
}

export interface BulkUploadParams {
  csvFile: File;
  files: File[];
  organization_id: string;
  project_id: string;
}

export interface DocumentReference {
  documentId: string;
  linkType: "direct" | "indirect";
}

export interface Enclosure {
  id: string;
  filename: string;
  presigned_url: string;
  filetype: string;
  filesize: number;
  uploadedAt: string;
  uploadedBy: string;
}

export interface Tag {
  _id: string;
  name: string;
  description?: string;
}

export interface Role {
  _id: string;
  name: string;
  permissions: string[];
  description?: string;
  level?: string;
}

export interface Permission {
  _id: string;
  name: string;
  description?: string;
}

export interface ReportDefinition {
  id: string;
  name: string;
  description: string;
  category: "letters" | "documents" | "tasks";
  defaultColumns: string[];
  metrics: string[];
  downloadFormats: string[];
}

export interface ReportPreviewResponse {
  reportId: string;
  reportName: string;
  generatedAt: string;
  columns: string[];
  rows: Array<Record<string, any>>;
  metrics: Record<string, any>;
  totalRows: number;
}

export type LetterTemplateStatus = "active" | "draft" | "archived";
export type LetterTemplateVisibility = "global" | "organization" | "project";

export interface LetterTemplateSection {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
  content: string;
  order: number;
}

export interface LetterTemplateVersion {
  version: number;
  updated_at: string;
  updated_by?: string;
  description?: string;
  sections: LetterTemplateSection[];
}

export interface LetterTemplate {
  _id: string;
  name: string;
  code: string;
  category: string;
  description?: string;
  status: LetterTemplateStatus;
  visibility: LetterTemplateVisibility;
  organization_id?: string;
  project_id?: string;
  sections: LetterTemplateSection[];
  version: number;
  versions?: LetterTemplateVersion[];
  usage_count?: number;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
  updated_by?: string;
}

export interface LetterTemplateListResponse {
  templates: LetterTemplate[];
  total: number;
  page: number;
  limit: number;
  has_next?: boolean;
  has_prev?: boolean;
}

export interface LetterTemplateCreate {
  name: string;
  code: string;
  category: string;
  description?: string;
  status?: LetterTemplateStatus;
  visibility?: LetterTemplateVisibility;
  organization_id?: string;
  project_id?: string;
  sections?: LetterTemplateSection[];
}

export type LetterTemplateUpdate = Partial<LetterTemplateCreate>;

export interface ReportRequestPayload {
  reportId: string;
  startDate?: string;
  endDate?: string;
  organizationId?: string;
  projectId?: string;
  limit?: number;
  letterNo?: string;
  chainDirection?: "up" | "down";
  includeSelf?: boolean;
  direction?: "incoming" | "outgoing" | "both";
  tags?: string[];
  subTags?: string[];
  statuses?: string[];
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface AuthToken {
  access_token: string;
  token_type: string;
}

export interface CreateUserData {
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  password: string;
  roles?: string[];
  organization_id?: string;
  projects?: string[];
  organizations?: string[];
  permissions?: string[];
  preferences?: UserPreferences;
}

export interface UpdateUserData {
  username?: string;
  email?: string;
  password?: string;
  roles?: string[];
  disabled?: boolean;
  first_name?: string;
  last_name?: string;
  organization_id?: string;
  projects?: string[];
  organizations?: string[];
  permissions?: string[];
  preferences?: UserPreferences;
}

export interface DocumentUploadData {
  file: File;
  organization_id: string;
  project_id: string;
  uploadType: "incoming" | "outgoing";
  letterNo?: string;
  date: string;
  subject?: string;
  from?: string;
  to?: string;
  tags?: string[];
  subTags?: string[];
  status?: string;
  ocrEnabled?: boolean;
  compressionEnabled?: boolean;
}

export interface DocumentUpdateData {
  uploadType?: "incoming" | "outgoing";
  letterNo?: string;
  date?: string;
  subject?: string;
  // Support both keys; backend model uses Field(alias="from")
  from_?: string;
  from?: string;
  to?: string;
  tags?: string[];
  subTags?: string[];
  status?: string;
  ocrEnabled?: boolean;
  compressionEnabled?: boolean;
}

export interface LinkDocumentsRequest {
  document_a_id: string;
  document_b_id: string;
  link_type: "incoming" | "outgoing";
}

export interface ReferenceCreateData {
  referenced_document_id: string;
  link_type: "direct" | "indirect";
}

export interface DeepPlanningResponse {
  draft_letter: string;
  extracted_key_points: string;
  quoted_clauses: Array<{
    clause_number: string;
    page_number?: string;
    line_numbers?: string;
    content: string;
  }>;
  similar_letters: Array<{
    id: string;
    title: string;
    subject: string;
    content: string;
    recipient: string;
    similarity_score: number;
    created_at: string;
  }>;
  structure_summary: string;
}

export const createProjectRepresentative = (
  projectId: string,
  data: RepresentativeCreateInput,
) => {
  return axios
    .post(joinApiUrl(`/projects/${projectId}/representatives`), data)
    .then((res) => res.data);
};

export const getProjectRepresentatives = (projectId: string) => {
  return axios
    .get(joinApiUrl(`/projects/${projectId}/representatives`))
    .then((res) => res.data);
};

export const updateProjectRepresentative = (
  projectId: string,
  repId: string,
  data: RepresentativeUpdateInput,
) => {
  return axios
    .put(joinApiUrl(`/projects/${projectId}/representatives/${repId}`), data)
    .then((res) => res.data);
};

export const deleteProjectRepresentative = (
  projectId: string,
  repId: string,
) => {
  return axios
    .delete(joinApiUrl(`/projects/${projectId}/representatives/${repId}`))
    .then((res) => res.data);
};

// ---- PARTY Representatives (optional, pattern only, enable if needed) ----

export const createPartyRepresentative = (
  partyId: string,
  data: RepresentativeCreateInput,
) => {
  return axios
    .post(joinApiUrl(`/parties/${partyId}/representatives`), data)
    .then((res) => res.data);
};

export const getPartyRepresentatives = (partyId: string) => {
  return axios
    .get(joinApiUrl(`/parties/${partyId}/representatives`))
    .then((res) => res.data);
};

export const updatePartyRepresentative = (
  partyId: string,
  repId: string,
  data: RepresentativeUpdateInput,
) => {
  return axios
    .put(joinApiUrl(`/parties/${partyId}/representatives/${repId}`), data)
    .then((res) => res.data);
};

export const deletePartyRepresentative = (partyId: string, repId: string) => {
  return axios
    .delete(joinApiUrl(`/parties/${partyId}/representatives/${repId}`))
    .then((res) => res.data);
};

class EnhancedApiService {
  private baseURL = API_BASE_URL;
  private normalizeData<T>(data: any): T {
    if (Array.isArray(data)) {
      return data.map((item) => this.normalizeData(item)) as any;
    }
    if (data && typeof data === "object") {
      return {
        ...data,
        // Ensure _id exists if id is present
        _id: data._id || data.id,
        // Ensure organization_id is consistently named
        organization_id: data.organization_id || data.organizationId,
      };
    }
    return data;
  }
  private async request<T>(
    endpoint: string,
    options?: RequestInit & { responseType?: "json" | "blob" | "text" },
  ): Promise<T> {
    const { responseType = "json", ...requestOptions } = options ?? {};

    // Prompt to extend session if token near expiry
    await ensureValidToken(120);

    const headers: Record<string, string> = {};

    // Copy existing headers if they exist
    if (requestOptions.headers) {
      const existingHeaders = requestOptions.headers as Record<string, string>;
      Object.assign(headers, existingHeaders);
    }

    // Only add Content-Type for non-FormData requests
    if (!(requestOptions.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }

    // Helper to perform fetch
    const doFetch = async () =>
      authenticatedFetch(`${this.baseURL}${endpoint}`, {
        ...requestOptions,
        credentials: requestOptions.credentials ?? "include",
        headers,
      });

    let response = await doFetch();

    // If unauthorized, attempt a single refresh then retry once
    if (response.status === 401) {
      try {
        await refreshToken();
        response = await doFetch();
      } catch {
        redirectToLoginAfterSessionExpiry();
        throw new Error("Session expired. Redirecting to login.");
      }
    }

    if (response.status === 403) {
      // Permission denied should surface a clear message to the UI
      let detail = "Permission denied";
      try {
        const parsed = await response.json();
        detail = parsed?.detail || parsed?.message || detail;
      } catch {
        // ignore parse errors
      }
      throw new Error(detail);
    }

    if (!response.ok) {
      const errorText = await response.text();
      try {
        const errorData: ApiError = JSON.parse(errorText);
        throw new Error(
          errorData.detail || errorData.message || "An error occurred",
        );
      } catch {
        throw new Error(errorText || `HTTP error! status: ${response.status}`);
      }
    }

    if (responseType === "blob") {
      return (await response.blob()) as T;
    }

    if (responseType === "text") {
      return (await response.text()) as T;
    }

    const data = await response.json();
    return data as T;
  }

  // Authentication
  async login(credentials: LoginCredentials): Promise<AuthToken> {
    return this.request<AuthToken>("/login", {
      method: "POST",
      body: JSON.stringify(credentials),
    });
  }

  // Users
  async getUsers(): Promise<User[]> {
    const res = await this.request<any>("/users");
    // Support both array and paginated/object responses:
    // - [User, ...]
    // - { users: [...], total, page, limit }
    // - { items: [...], total, ... } (fallback)
    const list: any[] = Array.isArray(res)
      ? res
      : res?.users || res?.items || [];
    return (list || []) as User[];
  }

  async getUser(id: string): Promise<User> {
    return this.request<User>(`/users/${id}`);
  }

  async createUser(userData: CreateUserData): Promise<User> {
    return this.request<User>("/users", {
      method: "POST",
      body: JSON.stringify(userData),
    });
  }

  async updateUser(id: string, userData: UpdateUserData): Promise<User> {
    return this.request<User>(`/users/${id}`, {
      method: "PUT",
      body: JSON.stringify(userData),
    });
  }

  async deleteUser(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/users/${id}`, {
      method: "DELETE",
    });
  }

  // Account lock/unlock are step-up gated (users:lock / users:unlock).
  async lockUser(
    id: string,
    options?: { stepUpToken?: string },
  ): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/users/${id}/lock`, {
      method: "POST",
      headers: options?.stepUpToken
        ? { "X-Step-Up-Token": options.stepUpToken }
        : undefined,
    });
  }

  async unlockUser(
    id: string,
    options?: { stepUpToken?: string },
  ): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/users/${id}/unlock`, {
      method: "POST",
      headers: options?.stepUpToken
        ? { "X-Step-Up-Token": options.stepUpToken }
        : undefined,
    });
  }

  async checkUserPermission(
    permissionName: string,
  ): Promise<{ granted: boolean }> {
    const res = await this.request<any>("/permissions/check", {
      method: "POST",
      body: JSON.stringify({ permission: permissionName }),
    });
    if (typeof res === "object" && res !== null && "granted" in res) {
      return { granted: Boolean(res.granted) };
    }
    return { granted: Boolean(res) };
  }

  // Organizations
  async getOrganizations(): Promise<Organization[]> {
    // Normalize backend response which may be an array or an object { organizations: [...] }
    const res = await this.request<any>("/organizations");
    const list: any[] = Array.isArray(res) ? res : res?.organizations || [];
    return (list || []).map((org) => {
      if (!org) return org as Organization;
      const normalized: any = { ...org, _id: org._id || org.id };
      delete normalized.id;
      return normalized as Organization;
    });
  }

  async getOrganization(id: string): Promise<Organization> {
    const data = await this.request<any>(`/organizations/${id}`);
    const normalized: any = { ...data, _id: data?._id || data?.id };
    delete normalized.id;
    return normalized as Organization;
  }

  async createOrganization(
    orgData: Omit<Organization, "_id">,
  ): Promise<Organization> {
    return this.request<Organization>("/organizations", {
      method: "POST",
      body: JSON.stringify(orgData),
    });
  }

  async updateOrganization(
    id: string,
    orgData: Partial<Organization>,
  ): Promise<Organization> {
    return this.request<Organization>(`/organizations/${id}`, {
      method: "PUT",
      body: JSON.stringify(orgData),
    });
  }

  async deleteOrganization(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/organizations/${id}`, {
      method: "DELETE",
    });
  }

  // Projects
  async getProjects(): Promise<Project[]> {
    const res = await this.request<any>("/projects");
    const list: any[] = Array.isArray(res)
      ? res
      : res?.projects || res?.items || [];
    return (list || []).map((p) => {
      if (!p) return p as Project;
      // Normalize id fields and organization reference casing
      const normalized: any = {
        ...p,
        _id: p._id ?? p.id,
        organization_id: p.organization_id ?? p.organizationId ?? "",
      };
      delete normalized.id;
      return normalized as Project;
    });
  }

  async getProject(id: string): Promise<Project> {
    const data = await this.request<any>(`/projects/${id}`);
    const normalized: any = {
      ...data,
      _id: data?._id ?? data?.id,
      organization_id: data?.organization_id ?? data?.organizationId ?? "",
    };
    delete normalized.id;
    return normalized as Project;
  }

  async getProjectById(id: string): Promise<Project> {
    return this.getProject(id);
  }

  async createProject(projectData: Omit<Project, "_id">): Promise<Project> {
    return this.request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(projectData),
    });
  }

  async updateProject(
    id: string,
    projectData: Partial<Project>,
  ): Promise<Project> {
    return this.request<Project>(`/projects/${id}`, {
      method: "PUT",
      body: JSON.stringify(projectData),
    });
  }

  async deleteProject(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/projects/${id}`, {
      method: "DELETE",
    });
  }

  async getNotifications(
    params: {
      unread_only?: boolean;
      category?: string;
      event_type?: string;
      project_id?: string;
      search?: string;
      limit?: number;
      skip?: number;
    } = {},
  ): Promise<NotificationListResponse> {
    const search = new URLSearchParams();
    if (params.unread_only !== undefined) {
      search.set("unread_only", String(params.unread_only));
    }
    if (params.category) {
      search.set("category", params.category);
    }
    if (params.event_type) {
      search.set("event_type", params.event_type);
    }
    if (params.project_id) {
      search.set("project_id", params.project_id);
    }
    if (params.search) {
      search.set("search", params.search);
    }
    if (params.limit !== undefined) {
      search.set("limit", String(params.limit));
    }
    if (params.skip !== undefined) {
      search.set("skip", String(params.skip));
    }
    const qs = search.toString();
    return this.request<NotificationListResponse>(
      `/notifications${qs ? `?${qs}` : ""}`,
    );
  }

  async markNotificationRead(id: string): Promise<{ status: string }> {
    return this.request<{ status: string }>(`/notifications/${id}/read`, {
      method: "PATCH",
    });
  }

  async executeNotificationAction(
    id: string,
    action: string,
    payload: Record<string, any> = {},
  ): Promise<{ status: string; action: string; result: Record<string, any> }> {
    return this.request<{ status: string; action: string; result: Record<string, any> }>(
      `/notifications/${id}/action`,
      {
        method: "POST",
        body: JSON.stringify({ action, payload }),
      },
    );
  }

  async markAllNotificationsRead(filters?: {
    category?: string;
    event_type?: string;
    project_id?: string;
  }): Promise<{ updated: number }> {
    return this.request<{ updated: number }>(`/notifications/read-all`, {
      method: "POST",
      body: filters ? JSON.stringify(filters) : undefined,
    });
  }

  async getUnreadNotificationCount(): Promise<{ unread_count: number }> {
    return this.request<{ unread_count: number }>(
      `/notifications/unread-count`,
    );
  }

  async getNotificationPreferences(): Promise<NotificationPreference> {
    return this.request<NotificationPreference>(`/notifications/preferences`);
  }

  async updateNotificationPreferences(
    data: NotificationPreferenceUpdate,
  ): Promise<NotificationPreference> {
    return this.request<NotificationPreference>(`/notifications/preferences`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  async sendNotificationTestEmail(data?: {
    target_user_id?: string;
    event_type?: string;
  }): Promise<{ sent: boolean; delivery_log?: Record<string, any> }> {
    return this.request<{ sent: boolean; delivery_log?: Record<string, any> }>(
      `/notification-test/email`,
      {
        method: "POST",
        body: JSON.stringify(data || {}),
      },
    );
  }

  async getProjectNotificationSettings(
    projectId: string,
  ): Promise<ProjectNotificationSubscription> {
    return this.request<ProjectNotificationSubscription>(
      `/projects/${projectId}/notification-settings`,
    );
  }

  async updateProjectNotificationSettings(
    projectId: string,
    data: Partial<Pick<ProjectNotificationSubscription, "subscribed" | "event_settings">>,
  ): Promise<ProjectNotificationSubscription> {
    return this.request<ProjectNotificationSubscription>(
      `/projects/${projectId}/notification-settings`,
      {
        method: "PATCH",
        body: JSON.stringify(data),
      },
    );
  }

  // Soft-deactivate a project (superadmin only, enforced by backend)
  async deactivateProject(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/projects/${id}/deactivate`, {
      method: "POST",
    });
  }

  // Documents
  async getDocuments(params?: {
    organization_id?: string;
    project_id?: string;
    tags?: string[];
    subTags?: string[];
    letterNo?: string[];
    date?: string;
    subject?: string[];
    from_?: string[];
    to?: string[];
    uploadType?: "incoming" | "outgoing";
    status?: string;
    skip?: number;
    limit?: number;
  }): Promise<Document[]> {
    const searchParams = new URLSearchParams();
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined) {
          if (Array.isArray(value)) {
            value.forEach((v) => searchParams.append(key, v));
          } else {
            searchParams.append(key, value.toString());
          }
        }
      });
    }

    const queryString = searchParams.toString();
    const endpoint = `/documents${queryString ? `?${queryString}` : ""}`;
    const res = await this.request<any>(endpoint);
    // Support both array and { documents, total } shapes
    if (Array.isArray(res)) {
      return res as Document[];
    }
    if (res && Array.isArray(res.documents)) {
      return res.documents as Document[];
    }
    return [];
  }

  async getDocument(id: string): Promise<Document> {
    return this.request<Document>(`/documents/${id}`);
  }

  async getDocumentProcessingStatus(
    id: string,
  ): Promise<DocumentProcessingJobStatus> {
    return this.request<DocumentProcessingJobStatus>(
      `/documents/${id}/processing-status`,
    );
  }

  async uploadDocument(documentData: DocumentUploadData): Promise<Document> {
    const formData = new FormData();
    formData.append("file", documentData.file);
    formData.append("organization_id", documentData.organization_id);
    formData.append("project_id", documentData.project_id);
    formData.append("uploadType", documentData.uploadType);
    formData.append("date", documentData.date);
    formData.append("subject", documentData.subject ?? documentData.file.name);
    formData.append("status", documentData.status ?? "draft");
    formData.append("doc_status", documentData.status ?? "draft");

    if (documentData.letterNo) {
      formData.append("letterNo", documentData.letterNo);
    }
    if (documentData.from) {
      formData.append("from", documentData.from);
    }
    if (documentData.to) {
      formData.append("to", documentData.to);
    }
    if (documentData.tags) {
      documentData.tags.forEach((tag) => formData.append("tags", tag));
    }
    if (documentData.subTags) {
      documentData.subTags.forEach((subTag) =>
        formData.append("subTags", subTag),
      );
    }

    formData.append(
      "ocrEnabled",
      (documentData.ocrEnabled ?? false).toString(),
    );
    formData.append(
      "compressionEnabled",
      (documentData.compressionEnabled ?? false).toString(),
    );

    return this.request<Document>("/documents", {
      method: "POST",
      body: formData,
    });
  }

  async startBulkUpload(params: BulkUploadParams): Promise<BulkUploadResponse> {
    const formData = new FormData();
    formData.append("csv_file", params.csvFile);
    params.files.forEach((file) => {
      const relativePath =
        (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
        file.name;
      formData.append("files", file, relativePath);
    });
    formData.append("organization_id", params.organization_id);
    formData.append("project_id", params.project_id);

    return this.request<BulkUploadResponse>("/documents/bulk-upload", {
      method: "POST",
      body: formData,
    });
  }

  async getBulkUploadStatus(jobId: string): Promise<BulkUploadStatus> {
    return this.request<BulkUploadStatus>(
      `/documents/bulk-upload/${jobId}/status`,
    );
  }

  async downloadBulkUploadTemplate(): Promise<Blob> {
    await ensureValidToken(120);

    const headers: Record<string, string> = {
      Accept: "text/csv",
    };

    const doFetch = async () =>
      authenticatedFetch(`${this.baseURL}/documents/bulk-upload/template`, {
        method: "GET",
        headers,
      });

    let response = await doFetch();

    if (response.status === 401) {
      try {
        await refreshToken();
        response = await doFetch();
      } catch {
        redirectToLoginAfterSessionExpiry();
        throw new Error("Session expired. Redirecting to login.");
      }
    }

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || `HTTP error! status: ${response.status}`);
    }

    return response.blob();
  }

  async updateDocument(
    id: string,
    documentData: DocumentUpdateData,
  ): Promise<Document> {
    return this.request<Document>(`/documents/${id}`, {
      method: "PUT",
      body: JSON.stringify(documentData),
    });
  }

  async deleteDocument(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/documents/${id}`, {
      method: "DELETE",
    });
  }

  // Draft workflow guards for documents
  async requestDraftForDocument(id: string): Promise<Document> {
    return this.request<Document>(`/documents/${id}/request-draft`, {
      method: "POST",
    });
  }

  async completeDraftForDocument(id: string): Promise<Document> {
    return this.request<Document>(`/documents/${id}/complete-draft`, {
      method: "POST",
    });
  }

  // Document References
  async getDocumentReferences(id: string): Promise<DocumentReference[]> {
    return this.request<DocumentReference[]>(`/documents/${id}/references`);
  }

  async addDocumentReference(
    id: string,
    referenceData: ReferenceCreateData,
  ): Promise<Document> {
    return this.request<Document>(`/documents/${id}/references`, {
      method: "POST",
      body: JSON.stringify(referenceData),
    });
  }

  async deleteDocumentReference(
    id: string,
    referenceId: string,
  ): Promise<Document> {
    return this.request<Document>(
      `/documents/${id}/references/${referenceId}`,
      {
        method: "DELETE",
      },
    );
  }

  // Document Enclosures
  async getDocumentEnclosures(id: string): Promise<Enclosure[]> {
    return this.request<Enclosure[]>(`/documents/${id}/enclosures`);
  }

  async addDocumentEnclosure(id: string, file: File): Promise<Enclosure> {
    const formData = new FormData();
    formData.append("file", file);

    return this.request<Enclosure>(`/documents/${id}/enclosures`, {
      method: "POST",
      body: formData,
    });
  }

  async deleteDocumentEnclosure(
    id: string,
    enclosureId: string,
  ): Promise<Document> {
    return this.request<Document>(
      `/documents/${id}/enclosures/${enclosureId}`,
      {
        method: "DELETE",
      },
    );
  }

  // Document Linking
  async linkDocuments(
    linkData: LinkDocumentsRequest,
  ): Promise<{ message: string; linked_documents: string[] }> {
    return this.request<{ message: string; linked_documents: string[] }>(
      "/documents/link",
      {
        method: "POST",
        body: JSON.stringify(linkData),
      },
    );
  }

  async getLinkedDocuments(id: string): Promise<DocumentReference[]> {
    return this.request<DocumentReference[]>(`/documents/${id}/linked`);
  }

  // Parties (existing methods from original API service)
  async getParties(params?: {
    type?: string;
    search?: string;
    project_id?: string;
    skip?: number;
    limit?: number;
  }): Promise<Party[]> {
    const queryParams = new URLSearchParams();
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value === undefined || value === null || value === "") {
          return;
        }
        queryParams.append(key, String(value));
      });
    }
    const queryString = queryParams.toString() ? `?${queryParams}` : "";
    const response = await this.request<PartyListResponse>(
      `/parties/${queryString}`,
    );
    return response.parties;
  }

  async getParty(id: string): Promise<Party> {
    return this.request<Party>(`/parties/${id}`);
  }

  async createParty(
    party: Omit<Party, "_id" | "createdAt" | "representatives">,
  ): Promise<Party> {
    return this.request<Party>("/parties/", {
      method: "POST",
      body: JSON.stringify(party),
    });
  }

  async updateParty(id: string, party: Partial<Party>): Promise<Party> {
    return this.request<Party>(`/parties/${id}`, {
      method: "PUT",
      body: JSON.stringify(party),
    });
  }

  async deleteParty(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/parties/${id}`, {
      method: "DELETE",
    });
  }

  // Representatives
  async getRepresentatives(params?: {
    level?: "organization" | "project";
    organization_id?: string;
    project_id?: string;
    search?: string;
  }): Promise<Representative[]> {
    const queryString = params ? `?${new URLSearchParams(params)}` : "";
    return this.request<Representative[]>(`/representatives${queryString}`);
  }

  async createRepresentative(
    representative: RepresentativeCreateInput,
  ): Promise<Representative> {
    let url = "";
    if ("party_id" in representative && representative.party_id) {
      url = `/parties/${representative.party_id}/representatives`;
    } else if ("project_id" in representative && representative.project_id) {
      url = `/projects/${representative.project_id}/representatives`;
    } else if (
      "organization_id" in representative &&
      representative.organization_id
    ) {
      url = `/organizations/${representative.organization_id}/representatives`;
    } else {
      throw new Error(
        "Either party_id, project_id, or organization_id must be provided",
      );
    }
    return this.request<Representative>(url, {
      method: "POST",
      body: JSON.stringify(representative),
    });
  }

  async updateRepresentative(
    repId: string,
    representative: Partial<Representative>,
  ): Promise<Representative> {
    return this.request<Representative>(`/representatives/${repId}`, {
      method: "PUT",
      body: JSON.stringify(representative),
    });
  }

  async deleteRepresentative(repId: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/representatives/${repId}`, {
      method: "DELETE",
    });
  }

  // Convenience wrappers expected by some pages (PartiesInvolvedPage)
  // Project-level reps
  async getProjectRepresentatives(
    projectId: string,
  ): Promise<Representative[]> {
    return this.request<Representative[]>(
      `/projects/${projectId}/representatives`,
    );
  }
  async addProjectRepresentative(
    projectId: string,
    data: RepresentativeCreateInput,
  ): Promise<Representative> {
    return this.request<Representative>(
      `/projects/${projectId}/representatives`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    );
  }

  // Organization-level reps
  async getOrganizationRepresentatives(
    organizationId: string,
  ): Promise<Representative[]> {
    return this.request<Representative[]>(
      `/organizations/${organizationId}/representatives`,
    );
  }
  async addOrganizationRepresentative(
    organizationId: string,
    data: RepresentativeCreateInput,
  ): Promise<Representative> {
    return this.request<Representative>(
      `/organizations/${organizationId}/representatives`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    );
  }

  // Party-level reps
  async addRepresentative(
    partyId: string,
    data: RepresentativeCreateInput,
  ): Promise<Representative> {
    return this.request<Representative>(`/parties/${partyId}/representatives`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  // ID-based helpers (naming expected by FE)
  async updateRepresentativeById(
    repId: string,
    representative: Partial<Representative>,
  ): Promise<Representative> {
    return this.updateRepresentative(repId, representative);
  }

  async deleteRepresentativeById(repId: string): Promise<{ message: string }> {
    return this.deleteRepresentative(repId);
  }

  // Tags
  async getTags(): Promise<Tag[]> {
    const res = await this.request<any>("/tags");
    if (Array.isArray(res)) {
      return res as Tag[];
    }
    if (res && Array.isArray(res.tags)) {
      return res.tags as Tag[];
    }
    console.warn("Unexpected tags response shape:", res);
    return [];
  }

  async getTag(id: string): Promise<Tag> {
    return this.request<Tag>(`/tags/${id}`);
  }

  async createTag(tagData: Omit<Tag, "_id">): Promise<Tag> {
    return this.request<Tag>("/tags", {
      method: "POST",
      body: JSON.stringify(tagData),
    });
  }

  async updateTag(id: string, tagData: Partial<Tag>): Promise<Tag> {
    return this.request<Tag>(`/tags/${id}`, {
      method: "PUT",
      body: JSON.stringify(tagData),
    });
  }

  async deleteTag(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/tags/${id}`, {
      method: "DELETE",
    });
  }

  // Letter Templates
  async getLetterTemplates(params?: {
    search?: string;
    category?: string;
    status?: LetterTemplateStatus;
    visibility?: LetterTemplateVisibility;
    organization_id?: string;
    project_id?: string;
  }): Promise<LetterTemplate[]> {
    const searchParams = new URLSearchParams();
    if (params?.search) searchParams.set("search", params.search);
    if (params?.category) searchParams.set("category", params.category);
    if (params?.status) searchParams.set("status", params.status);
    if (params?.visibility) searchParams.set("visibility", params.visibility);
    if (params?.organization_id) {
      searchParams.set("organization_id", params.organization_id);
    }
    if (params?.project_id) {
      searchParams.set("project_id", params.project_id);
    }
    const qs = searchParams.toString();
    const res = await this.request<any>(
      `/letter-templates${qs ? `?${qs}` : ""}`,
    );
    const list: any[] = Array.isArray(res) ? res : res?.templates || [];
    return list.map((template) => {
      if (!template) return template as LetterTemplate;
      return {
        ...template,
        _id: template._id || template.id,
        organization_id:
          template.organization_id || template.organizationId || undefined,
        project_id: template.project_id || template.projectId || undefined,
        sections: Array.isArray(template.sections) ? template.sections : [],
      } as LetterTemplate;
    });
  }

  async getLetterTemplate(id: string): Promise<LetterTemplate> {
    const data = await this.request<any>(`/letter-templates/${id}`);
    return {
      ...data,
      _id: data?._id || data?.id,
      organization_id: data?.organization_id || data?.organizationId,
      project_id: data?.project_id || data?.projectId,
      sections: Array.isArray(data?.sections) ? data.sections : [],
    } as LetterTemplate;
  }

  async createLetterTemplate(
    data: LetterTemplateCreate,
  ): Promise<LetterTemplate> {
    const created = await this.request<any>("/letter-templates", {
      method: "POST",
      body: JSON.stringify(data),
    });
    return {
      ...created,
      _id: created?._id || created?.id,
      organization_id: created?.organization_id || created?.organizationId,
      project_id: created?.project_id || created?.projectId,
      sections: Array.isArray(created?.sections) ? created.sections : [],
    } as LetterTemplate;
  }

  async updateLetterTemplate(
    id: string,
    data: LetterTemplateUpdate,
  ): Promise<LetterTemplate> {
    const updated = await this.request<any>(`/letter-templates/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
    return {
      ...updated,
      _id: updated?._id || updated?.id,
      organization_id: updated?.organization_id || updated?.organizationId,
      project_id: updated?.project_id || updated?.projectId,
      sections: Array.isArray(updated?.sections) ? updated.sections : [],
    } as LetterTemplate;
  }

  async deleteLetterTemplate(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/letter-templates/${id}`, {
      method: "DELETE",
    });
  }
  // Subtags
  async getTagSubtags(tagId: string): Promise<any[]> {
    const res = await this.request<any>(`/tags/${tagId}/subtags`);
    if (Array.isArray(res)) {
      return res;
    }
    if (res && Array.isArray(res.subtags)) {
      return res.subtags;
    }
    console.warn("Unexpected subtags response shape:", res);
    return [];
  }

  // Roles
  async getRoles(): Promise<Role[]> {
    // Backend exposes GET /roles (no trailing slash)
    const res = await this.request<any>("/roles");
    // Normalize in case backend later wraps result
    const list: any[] = Array.isArray(res)
      ? res
      : res?.roles || res?.items || [];
    return (list || []) as Role[];
  }

  async getRole(id: string): Promise<Role> {
    return this.request<Role>(`/roles/${id}`);
  }

  async createRole(
    roleData: Omit<Role, "_id">,
    options?: { stepUpToken?: string },
  ): Promise<Role> {
    // Backend uses POST /roles (no trailing slash)
    return this.request<Role>("/roles", {
      method: "POST",
      headers: options?.stepUpToken
        ? { "X-Step-Up-Token": options.stepUpToken }
        : undefined,
      body: JSON.stringify(roleData),
    });
  }

  async processDocument(id: string): Promise<DocumentProcessingResult> {
    return this.request<DocumentProcessingResult>(`/documents/${id}/process`, {
      method: "POST",
    });
  }

  async updateRole(
    id: string,
    roleData: Partial<Role>,
    options?: { stepUpToken?: string },
  ): Promise<Role> {
    return this.request<Role>(`/roles/${id}`, {
      method: "PUT",
      headers: options?.stepUpToken
        ? { "X-Step-Up-Token": options.stepUpToken }
        : undefined,
      body: JSON.stringify(roleData),
    });
  }

  async deleteRole(
    id: string,
    options?: { stepUpToken?: string },
  ): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/roles/${id}`, {
      method: "DELETE",
      headers: options?.stepUpToken
        ? { "X-Step-Up-Token": options.stepUpToken }
        : undefined,
    });
  }

  // Role-permission links
  async getRolePermissions(roleId: string): Promise<Permission[]> {
    return this.request<Permission[]>(`/roles/${roleId}/permissions`);
  }

  async addRolePermission(roleId: string, permissionId: string): Promise<Role> {
    // Backend defines POST /roles/{roleId}/permissions/{permissionId}
    // Use POST primarily; some older variants may accept PUT, but prefer POST here.
    return this.request<Role>(`/roles/${roleId}/permissions/${permissionId}`, {
      method: "POST",
    });
  }

  async removeRolePermission(
    roleId: string,
    permissionId: string,
  ): Promise<Role> {
    // Prefer DELETE on the same endpoint; if backend does not support it,
    // the caller should handle errors gracefully.
    return this.request<Role>(`/roles/${roleId}/permissions/${permissionId}`, {
      method: "DELETE",
    });
  }

  // Permissions
  async getPermissions(): Promise<Permission[]> {
    // Backend returns Dict[str, Any] with "permissions" array at GET /permissions
    const res = await this.request<any>("/permissions");
    const list: any[] = Array.isArray(res)
      ? res
      : res?.permissions || res?.items || [];
    return (list || []) as Permission[];
  }

  async getPermission(id: string): Promise<Permission> {
    return this.request<Permission>(`/permissions/${id}`);
  }

  async createPermission(
    permissionData: Omit<Permission, "_id">,
  ): Promise<Permission> {
    return this.request<Permission>("/permissions", {
      method: "POST",
      body: JSON.stringify(permissionData),
    });
  }

  async updatePermission(
    id: string,
    permissionData: Partial<Permission>,
  ): Promise<Permission> {
    return this.request<Permission>(`/permissions/${id}`, {
      method: "PUT",
      body: JSON.stringify(permissionData),
    });
  }

  async deletePermission(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/permissions/${id}`, {
      method: "DELETE",
    });
  }

  // Profile
  async getProfile(): Promise<UserProfile & { profile_photo_url?: string }> {
    return this.request<UserProfile & { profile_photo_url?: string }>(
      "/profiles/me",
    );
  }

  async updateProfile(
    profileData: Partial<UserProfile> & { profile_photo_url?: string },
  ): Promise<UserProfile & { profile_photo_url?: string }> {
    return this.request<UserProfile & { profile_photo_url?: string }>(
      "/profiles/me",
      {
        method: "PUT",
        body: JSON.stringify(profileData),
      },
    );
  }

  async uploadMyProfilePhoto(
    file: File,
  ): Promise<UserProfile & { profile_photo_url?: string }> {
    const formData = new FormData();
    formData.append("file", file);
    return this.request<UserProfile & { profile_photo_url?: string }>(
      "/profiles/photo",
      {
        method: "POST",
        body: formData,
      },
    );
  }

  async changeMyPassword(payload: {
    current_password: string;
    new_password: string;
  }): Promise<{ message: string }> {
    return this.request<{ message: string }>("/profiles/change-password", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  // Folder Structure
  async getFolderStructure(): Promise<any> {
    return this.request<any>("/folder-structure");
  }

  async createFolder(folderData: any): Promise<any> {
    return this.request<any>("/folder-structure", {
      method: "POST",
      body: JSON.stringify(folderData),
    });
  }

  async updateFolder(id: string, folderData: any): Promise<any> {
    return this.request<any>(`/folder-structure/${id}`, {
      method: "PUT",
      body: JSON.stringify(folderData),
    });
  }

  async deleteFolder(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/folder-structure/${id}`, {
      method: "DELETE",
    });
  }

  // Email
  async sendEmail(emailData: {
    to: string[];
    subject: string;
    body: string;
    attachments?: File[];
  }): Promise<{ message: string }> {
    const formData = new FormData();
    formData.append("to", JSON.stringify(emailData.to));
    formData.append("subject", emailData.subject);
    formData.append("body", emailData.body);

    if (emailData.attachments) {
      emailData.attachments.forEach((file, index) => {
        formData.append(`attachment_${index}`, file);
      });
    }

    return this.request<{ message: string }>("/email/send", {
      method: "POST",
      body: formData,
    });
  }

  // Tasks
  async getTasks(): Promise<any[]> {
    return this.request<any[]>("/tasks");
  }

  async getTask(id: string): Promise<any> {
    return this.request<any>(`/tasks/${id}`);
  }

  async createTask(taskData: any): Promise<any> {
    return this.request<any>("/tasks", {
      method: "POST",
      body: JSON.stringify(taskData),
    });
  }

  async updateTask(id: string, taskData: any): Promise<any> {
    return this.request<any>(`/tasks/${id}`, {
      method: "PUT",
      body: JSON.stringify(taskData),
    });
  }

  async deleteTask(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/tasks/${id}`, {
      method: "DELETE",
    });
  }

  // Letters
  async getLetters(): Promise<any[]> {
    return this.request<any[]>("/letters");
  }

  async getLetter(id: string): Promise<any> {
    return this.request<any>(`/letters/${id}`);
  }

  async createLetter(letterData: any): Promise<any> {
    return this.request<any>("/letters", {
      method: "POST",
      body: JSON.stringify(letterData),
    });
  }

  async updateLetter(id: string, letterData: any): Promise<any> {
    return this.request<any>(`/letters/${id}`, {
      method: "PUT",
      body: JSON.stringify(letterData),
    });
  }

  async deleteLetter(id: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(`/letters/${id}`, {
      method: "DELETE",
    });
  }

  // Conversation & Reply Tracking for Letters

  async getConversationTree(letterId: string): Promise<any> {
    return this.request<any>(`/letters/${letterId}/conversation-tree`);
  }

  async getConversationSummary(conversationId: string): Promise<any> {
    return this.request<any>(`/letters/${conversationId}/conversation-summary`);
  }

  async reparentLetter(letterId: string, newParentId: string): Promise<any> {
    return this.request<any>(`/letters/${letterId}/reparent`, {
      method: "POST",
      body: JSON.stringify({ new_parent_id: newParentId }),
    });
  }

  // AI Assistant Methods
  async searchSimilarLetters(
    query: string,
    limit?: number,
  ): Promise<{
    similar_letters: Array<{
      id: string;
      title: string;
      subject: string;
      content: string;
      recipient: string;
      similarity_score: number;
      created_at: string;
    }>;
    query_embedding: number[];
  }> {
    return this.request<any>("/ai-assistant/search-letters", {
      method: "POST",
      body: JSON.stringify({
        query,
        limit: limit || 3,
      }),
    });
  }

  async generateLetterDraft(data: {
    subject: string;
    recipient: string;
    user_id: string;
    context?: string;
    points?: string;
    similar_letters?: Array<{
      id: string;
      title: string;
      subject: string;
      content: string;
      recipient: string;
      similarity_score: number;
      created_at: string;
    }>;
    target_letter_id?: string;
  }): Promise<{
    draft_letter: string;
    similar_letters: Array<{
      id: string;
      title: string;
      subject: string;
      content: string;
      recipient: string;
      similarity_score: number;
      created_at: string;
    }>;
    structure_summary: string;
  }> {
    const payload: any = { ...data };

    return this.request<any>("/ai-assistant/generate-draft", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  // Deep Planning - Enhanced letter draft with document context
  async deepPlanning(data: {
    document_ids: string[];
    subject: string;
    recipient: string;
    user_id: string;
    context?: string;
    points?: string;
    target_letter_id?: string;
  }): Promise<DeepPlanningResponse> {
    const payload: any = { ...data };

    return this.request<DeepPlanningResponse>("/deep-planning/generate-draft", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  // Reports & Analytics
  async getReports(): Promise<ReportDefinition[]> {
    try {
      const res = await this.request<any[]>("/reports");
      if (!Array.isArray(res)) {
        console.warn("Reports response is not an array:", res);
        return [];
      }
      return res.map((item) => this.normalizeReportDefinition(item));
    } catch (error) {
      console.error("Failed to fetch reports:", error);
      throw new Error("Failed to load reports catalog. Please try again.");
    }
  }

  async previewReport(
    payload: ReportRequestPayload,
  ): Promise<ReportPreviewResponse> {
    try {
      // Validate payload before sending
      if (!payload.reportId) {
        throw new Error("Report ID is required");
      }

      const res = await this.request<any>("/reports/preview", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      const normalized = this.normalizeReportPreview(res);

      // Validate response structure
      if (!normalized.reportId || !Array.isArray(normalized.columns)) {
        console.warn("Invalid report preview response structure:", normalized);
        throw new Error("Invalid report preview response");
      }

      return normalized;
    } catch (error) {
      console.error("Failed to generate report preview:", error);
      if (error instanceof Error) {
        throw error;
      }
      throw new Error("Failed to generate report preview. Please try again.");
    }
  }

  async downloadReport(payload: ReportRequestPayload): Promise<Blob> {
    try {
      if (!payload.reportId) {
        throw new Error("Report ID is required");
      }

      return await this.request<Blob>("/reports/download", {
        method: "POST",
        body: JSON.stringify(payload),
        responseType: "blob",
      });
    } catch (error) {
      console.error("Failed to download report:", error);
      if (error instanceof Error) {
        throw error;
      }
      throw new Error("Failed to download report. Please try again.");
    }
  }

  private normalizeReportDefinition(raw: any): ReportDefinition {
    if (!raw || typeof raw !== "object") {
      console.warn("Invalid report definition raw data:", raw);
      return {
        id: "",
        name: "Unknown Report",
        description: "",
        category: "documents",
        defaultColumns: [],
        metrics: [],
        downloadFormats: ["csv"],
      };
    }

    const columns = raw?.default_columns ?? raw?.defaultColumns ?? [];
    const downloads = raw?.download_formats ?? raw?.downloadFormats ?? ["csv"];
    const category = raw?.category ?? "documents";

    // Validate category
    const validCategories = ["letters", "documents", "tasks"];
    const normalizedCategory = validCategories.includes(category)
      ? (category as ReportDefinition["category"])
      : "documents";

    return {
      id: raw?.id ?? "",
      name: raw?.name ?? "Unknown Report",
      description: raw?.description ?? "",
      category: normalizedCategory,
      defaultColumns: Array.isArray(columns) ? columns : [],
      metrics: Array.isArray(raw?.metrics) ? raw.metrics : [],
      downloadFormats: Array.isArray(downloads) ? downloads : ["csv"],
    };
  }

  private normalizeReportPreview(raw: any): ReportPreviewResponse {
    if (!raw || typeof raw !== "object") {
      console.warn("Invalid report preview raw data:", raw);
      return {
        reportId: "",
        reportName: "",
        generatedAt: new Date().toISOString(),
        columns: [],
        rows: [],
        metrics: {},
        totalRows: 0,
      };
    }

    const columns = Array.isArray(raw?.columns) ? raw.columns : [];
    const rows = Array.isArray(raw?.rows) ? raw.rows : [];
    const metrics =
      typeof raw?.metrics === "object" && raw?.metrics !== null
        ? raw.metrics
        : {};

    // Calculate total rows if not provided
    const totalRows = raw?.total_rows ?? raw?.totalRows ?? rows.length;

    return {
      reportId: raw?.report_id ?? raw?.reportId ?? "",
      reportName: raw?.report_name ?? raw?.reportName ?? "",
      generatedAt:
        raw?.generated_at ?? raw?.generatedAt ?? new Date().toISOString(),
      columns,
      rows,
      metrics,
      totalRows,
    };
  }
}

// Export the singleton instance
export const enhancedApi = new EnhancedApiService();
export default enhancedApi;
