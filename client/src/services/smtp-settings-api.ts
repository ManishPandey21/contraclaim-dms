import { api } from "./api";

export type SmtpEncryption = "none" | "starttls" | "ssl_tls";
export type SmtpScope = "organization" | "project";

export interface SmtpSettings {
  _id?: string;
  scope_type: SmtpScope;
  organization_id: string;
  project_id?: string | null;
  host: string;
  port: number;
  username: string;
  sender_email: string;
  sender_name?: string | null;
  encryption: SmtpEncryption;
  is_active: boolean;
  password_configured: boolean;
  updated_at?: string;
}

export interface SmtpSettingsInput {
  host: string;
  port: number;
  username: string;
  password?: string;
  sender_email: string;
  sender_name?: string;
  encryption: SmtpEncryption;
  is_active: boolean;
}

export interface SmtpTestResponse {
  ok: boolean;
  source?: string | null;
  message: string;
}

const normalizeEncryption = (value: any): SmtpEncryption => {
  const raw = String(value || "starttls").toLowerCase();
  if (raw === "none") return "none";
  if (raw === "ssl" || raw === "ssl_tls") return "ssl_tls";
  return "starttls";
};

const normalizeSettings = (data: any): SmtpSettings => ({
  _id: data?._id ?? data?.id,
  scope_type: data?.scope_type ?? data?.level,
  organization_id: data?.organization_id ?? data?.organizationId ?? "",
  project_id: data?.project_id ?? data?.projectId ?? null,
  host: data?.host ?? data?.smtp_host ?? "",
  port: Number(data?.port ?? data?.smtp_port ?? 587),
  username: data?.username ?? "",
  sender_email: data?.sender_email ?? data?.senderEmail ?? "",
  sender_name: data?.sender_name ?? data?.senderName ?? "",
  encryption: normalizeEncryption(data?.encryption),
  is_active: Boolean(data?.is_active ?? data?.active ?? true),
  password_configured: Boolean(data?.password_configured ?? data?.password_set),
  updated_at: data?.updated_at ?? data?.updatedAt,
});

export async function getOrganizationSmtpSettings(
  organizationId: string,
): Promise<SmtpSettings | null> {
  try {
    const { data } = await api.get(`/smtp-settings/organization/${organizationId}`);
    return normalizeSettings(data);
  } catch (error: any) {
    if (error?.response?.status === 404) return null;
    throw error;
  }
}

export async function saveOrganizationSmtpSettings(
  organizationId: string,
  payload: SmtpSettingsInput,
  exists: boolean,
): Promise<SmtpSettings> {
  const { data } = exists
    ? await api.put(`/smtp-settings/organization/${organizationId}`, payload)
    : await api.post(`/smtp-settings/organization/${organizationId}`, payload);
  return normalizeSettings(data);
}

export async function testOrganizationSmtpSettings(
  organizationId: string,
): Promise<SmtpTestResponse> {
  const { data } = await api.post(`/smtp-settings/organization/${organizationId}/test`);
  return data;
}

export async function getProjectSmtpSettings(
  projectId: string,
): Promise<SmtpSettings | null> {
  try {
    const { data } = await api.get(`/smtp-settings/project/${projectId}`);
    return normalizeSettings(data);
  } catch (error: any) {
    if (error?.response?.status === 404) return null;
    throw error;
  }
}

export async function saveProjectSmtpSettings(
  projectId: string,
  payload: SmtpSettingsInput,
  exists: boolean,
): Promise<SmtpSettings> {
  const { data } = exists
    ? await api.put(`/smtp-settings/project/${projectId}`, payload)
    : await api.post(`/smtp-settings/project/${projectId}`, payload);
  return normalizeSettings(data);
}

export async function testProjectSmtpSettings(
  projectId: string,
): Promise<SmtpTestResponse> {
  const { data } = await api.post(`/smtp-settings/project/${projectId}/test`);
  return data;
}
