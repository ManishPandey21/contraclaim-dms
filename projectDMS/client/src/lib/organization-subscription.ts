import {
  type EffectivePlanState,
  type PlanSettingsScopePayload,
} from "@/services/plan-settings-api";

export type OrganizationSubscriptionSnapshot = {
  plan_code: string;
  billing_period: string;
};

export type OrganizationSubscriptionFormValues = {
  plan_code?: string;
  billing_period?: string;
  trial_enabled?: boolean;
  [key: string]: unknown;
};

export const NO_SERVICE_PLAN_CODE = "no_service_override";
export const DEFAULT_BILLING_PERIOD = "monthly";

export const organizationSubscriptionSnapshotFromEffective = (
  effective?: EffectivePlanState | null
): OrganizationSubscriptionSnapshot => ({
  plan_code: effective?.plan_code || NO_SERVICE_PLAN_CODE,
  billing_period: effective?.billing_period || DEFAULT_BILLING_PERIOD,
});

export const organizationSubscriptionSnapshotFromForm = (
  data: Partial<OrganizationSubscriptionFormValues>
): OrganizationSubscriptionSnapshot => ({
  plan_code: data.plan_code || NO_SERVICE_PLAN_CODE,
  billing_period: data.billing_period || DEFAULT_BILLING_PERIOD,
});

export const organizationSubscriptionChanged = (
  initial: OrganizationSubscriptionSnapshot | null,
  data: Partial<OrganizationSubscriptionFormValues>
) => {
  if (initial && !data.plan_code) {
    return false;
  }
  const next = organizationSubscriptionSnapshotFromForm(data);
  if (!initial) {
    return Boolean(data.plan_code && data.plan_code !== NO_SERVICE_PLAN_CODE);
  }
  return (
    initial.plan_code !== next.plan_code ||
    initial.billing_period !== next.billing_period
  );
};

export const buildOrganizationProfilePayload = <
  T extends OrganizationSubscriptionFormValues,
>(
  data: T
) => {
  const { plan_code, billing_period, trial_enabled, ...profile } = data;
  return profile;
};

export const buildOrganizationPlanScopePayload = (
  organizationId: string,
  data: Partial<OrganizationSubscriptionFormValues>
): PlanSettingsScopePayload => {
  const planCode = data.plan_code || NO_SERVICE_PLAN_CODE;
  const isNoService = planCode === NO_SERVICE_PLAN_CODE;
  return {
    organization_id: organizationId,
    project_id: null,
    mode: isNoService ? "no_service" : "plan",
    plan_code: isNoService ? null : planCode,
    billing_period: data.billing_period || DEFAULT_BILLING_PERIOD,
    status: "active",
  };
};
