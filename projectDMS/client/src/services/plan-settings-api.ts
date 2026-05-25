import { api } from "./api";

export interface PlanSettingsPlan {
  id?: string;
  code: string;
  name: string;
  family: string;
  features?: Record<string, unknown>;
  is_active?: boolean;
}

export interface PlanSettingsOrganization {
  id: string;
  name: string;
  [key: string]: unknown;
}

export interface PlanSettingsProject {
  id: string;
  name: string;
  organization_id?: string;
  organizationId?: string;
  [key: string]: unknown;
}

export interface EffectivePlanState {
  source: "organization" | "project" | "inherited" | "none";
  subscription_id?: string | null;
  plan_code?: string | null;
  plan_name?: string | null;
  features?: Record<string, unknown>;
  dms_enabled: boolean;
  drafting_enabled: boolean;
  inherited_from_organization_id?: string | null;
}

export interface PlanSettingsResponse {
  organizations: PlanSettingsOrganization[];
  projects: PlanSettingsProject[];
  plans: PlanSettingsPlan[];
  subscriptions: Record<string, unknown>[];
  effective: {
    organizations: Record<string, EffectivePlanState>;
    projects: Record<string, EffectivePlanState>;
  };
}

export type PlanSettingsScopeMode = "inherit" | "plan" | "no_service";

export interface PlanSettingsScopePayload {
  organization_id: string;
  project_id?: string | null;
  mode: PlanSettingsScopeMode;
  plan_code?: string | null;
  status?: "active";
}

export async function getPlanSettings(): Promise<PlanSettingsResponse> {
  const { data } = await api.get<PlanSettingsResponse>(
    "/rbac-monetization/plan-settings"
  );
  return data;
}

export async function updatePlanSettingsScope(
  payload: PlanSettingsScopePayload,
  options?: { stepUpToken?: string }
): Promise<PlanSettingsResponse> {
  const { data } = await api.put<PlanSettingsResponse>(
    "/rbac-monetization/plan-settings/scope",
    payload,
    {
      headers: options?.stepUpToken
        ? { "X-Step-Up-Token": options.stepUpToken }
        : undefined,
    }
  );
  return data;
}

export async function getEffectivePlanServices(): Promise<
  Pick<PlanSettingsResponse, "effective">
> {
  const { data } = await api.get<Pick<PlanSettingsResponse, "effective">>(
    "/rbac-monetization/plan-settings/effective-services"
  );
  return data;
}
