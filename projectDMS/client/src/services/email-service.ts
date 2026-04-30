import { API_BASE_URL } from "../config/api";

export interface EmailSuggestion {
  email: string;
  name: string;
  organization?: string;
}

export interface ShareDocumentRequest {
  recipient_email?: string;
  to?: string[];
  cc?: string[];
  bcc?: string[];
  subject: string;
  message: string;
  document_id: string;
  include_linked_documents?: boolean;
  include_letter_link?: boolean;
  include_refs?: boolean;
  reference_ids?: string[];
  group_ids?: string[];
  email_format?: "text" | "html";
  html_template?: string;
  registered_by?: string;
  distribution_for?: "answer" | "information";
}

export interface ShareDocumentResponse {
  message: string;
  document_name: string;
  attachments_count: number;
}

class EmailService {
  private baseUrl = API_BASE_URL;

  async getEmailSuggestions(
    query?: string,
    organizationId?: string,
    projectId?: string
  ): Promise<EmailSuggestion[]> {
    try {
      const params = new URLSearchParams();
      if (query) {
        params.append("query", query);
      }

      // Prefer explicit org/project passed by caller (e.g., from current letter)
      // Fallback to values from localStorage (current app context)
      const orgId =
        (organizationId ?? "").toString() ||
        window.localStorage.getItem("org_id") ||
        "";
      const projId =
        (projectId ?? "").toString() ||
        window.localStorage.getItem("proj_id") ||
        "";

      if (orgId) params.append("organization_id", orgId);
      if (projId) params.append("project_id", projId);

      const qs = params.toString();
      const url = `${this.baseUrl}/email/suggestions${qs ? `?${qs}` : ""}`;

      const response = await fetch(url, {
        headers: {
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
          "Content-Type": "application/json",
        },
      });

      if (!response.ok) {
        throw new Error(
          `Failed to fetch email suggestions: ${response.status}`
        );
      }

      return await response.json();
    } catch (error) {
      console.error("Error fetching email suggestions:", error);
      throw error;
    }
  }

  async resolveRecipients(params: {
    query?: string;
    include_representatives?: boolean;
    include_parties?: boolean;
    organization_id?: string;
    project_id?: string;
  }): Promise<EmailSuggestion[]> {
    try {
      const url = `${this.baseUrl}/email/resolve-recipients`;
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          query: params.query ?? "",
          include_representatives: params.include_representatives ?? true,
          include_parties: params.include_parties ?? true,
          organization_id: params.organization_id,
          project_id: params.project_id,
        }),
      });

      if (resp.status === 404) {
        // Fallback to GET suggestions
        return this.getEmailSuggestions(
          params.query,
          params.organization_id,
          params.project_id
        );
      }
      if (!resp.ok) {
        throw new Error(`Failed to resolve recipients: ${resp.status}`);
      }
      return await resp.json();
    } catch (error) {
      // Ultimate fallback to GET suggestions
      return this.getEmailSuggestions(
        params.query,
        params.organization_id,
        params.project_id
      );
    }
  }

  async shareDocument(
    request: ShareDocumentRequest
  ): Promise<ShareDocumentResponse> {
    try {
      const response = await fetch(`${this.baseUrl}/email/share-document`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(
          errorData.detail || `Failed to share document: ${response.status}`
        );
      }

      return await response.json();
    } catch (error) {
      console.error("Error sharing document:", error);
      throw error;
    }
  }
}

export const emailService = new EmailService();
