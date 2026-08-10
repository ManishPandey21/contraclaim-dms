import { api } from "./api";

// ---------------------------------------------------------------------------
// Shared types
// ---------------------------------------------------------------------------

export interface PlanSettingsPlan {
  id?: string;
  code: string;
  name: string;
  description?: string;
  family: string;
  tier?: number;
  billing_cadence?: string;
  currency?: string;
  base_price_minor?: number;
  pricing_tiers?: Record<string, number>;
  discount_percentages?: Record<string, number>;
  features?: Record<string, unknown>;
  default_limits?: Record<string, unknown>;
  available_add_ons?: string[];
  trial_config?: Record<string, unknown>;
  is_active?: boolean;
  display_order?: number;
  highlight?: boolean;
  max_users?: number | null;
  max_storage_gb?: number | null;
}

export interface PlanAddOn {
  id?: string;
  code: string;
  name: string;
  description?: string;
  add_on_type: "feature" | "capacity" | "support";
  price_minor: number;
  billing_cadence?: string;
  currency?: string;
  features?: Record<string, unknown>;
  limits?: Record<string, unknown>;
  compatible_plans?: string[];
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
  status?: string | null;
  billing_status?: string | null;
  billing_period?: string | null;
  current_period_start?: string | null;
  current_period_end?: string | null;
  trial?: boolean;
  trial_ends_at?: string | null;
  active_add_ons?: string[];
  features?: Record<string, unknown>;
  dms_enabled: boolean;
  drafting_enabled: boolean;
  inherited_from_organization_id?: string | null;
}

export interface CurrentEntitlements {
  contract_version: string;
  scope_mode: string;
  organization_id?: string | null;
  project_id?: string | null;
  source: string;
  plan_code?: string | null;
  features: Record<string, unknown>;
  dms_enabled: boolean;
  drafting_enabled: boolean;
  unavailable_reason?: string | null;
}

let currentEntitlementsInFlight:
  | { scopeKey: string; request: Promise<CurrentEntitlements> }
  | null = null;

function currentEntitlementScopeKey(): string {
  if (typeof window === "undefined") return ":";
  return `${window.localStorage.getItem("org_id") || ""}:${
    window.localStorage.getItem("proj_id") || ""
  }`;
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
  billing_period?: string | null;
  status?: "active";
}

export type BillingPeriod = "monthly" | "quarterly" | "semi_annual" | "annual";

// ---------------------------------------------------------------------------
// Plan Catalog
// ---------------------------------------------------------------------------

export interface PlanCatalogResponse {
  plans: PlanSettingsPlan[];
  add_ons: PlanAddOn[];
  billing_periods: string[];
  currency: string;
}

export async function getPlanCatalog(): Promise<PlanCatalogResponse> {
  const { data } = await api.get<PlanCatalogResponse>("/rbac-monetization/plan-catalog");
  return data;
}

export async function getPlanDetail(planCode: string): Promise<PlanSettingsPlan> {
  const { data } = await api.get<PlanSettingsPlan>(`/rbac-monetization/plans/${planCode}`);
  return data;
}

// ---------------------------------------------------------------------------
// Plan Settings (original endpoints)
// ---------------------------------------------------------------------------

export async function getPlanSettings(): Promise<PlanSettingsResponse> {
  const { data } = await api.get<PlanSettingsResponse>("/rbac-monetization/plan-settings");
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

export async function getCurrentEntitlements(): Promise<CurrentEntitlements> {
  const scopeKey = currentEntitlementScopeKey();
  if (currentEntitlementsInFlight?.scopeKey === scopeKey) {
    return currentEntitlementsInFlight.request;
  }

  const request = api
    .get<CurrentEntitlements>("/rbac-monetization/entitlements/me")
    .then(({ data }) => data)
    .finally(() => {
      if (currentEntitlementsInFlight?.request === request) {
        currentEntitlementsInFlight = null;
      }
    });
  currentEntitlementsInFlight = { scopeKey, request };
  return request;
}

// ---------------------------------------------------------------------------
// Add-Ons
// ---------------------------------------------------------------------------

export async function listAddOns(): Promise<PlanAddOn[]> {
  const { data } = await api.get<PlanAddOn[]>("/rbac-monetization/add-ons");
  return data;
}

// ---------------------------------------------------------------------------
// Subscriptions
// ---------------------------------------------------------------------------

export interface Subscription {
  id: string;
  organization_id: string;
  project_id?: string | null;
  plan_code: string;
  billing_period: string;
  status: string;
  billing_status: string;
  starts_at?: string | null;
  ends_at?: string | null;
  current_period_start?: string | null;
  current_period_end?: string | null;
  trial: boolean;
  trial_ends_at?: string | null;
  pilot: boolean;
  auto_renew: boolean;
  active_add_ons: string[];
  cancelled_at?: string | null;
  cancellation_reason?: string | null;
  created_at?: string;
  updated_at?: string;
}

export async function listSubscriptions(organizationId?: string): Promise<Subscription[]> {
  const params = organizationId ? { organization_id: organizationId } : {};
  const { data } = await api.get<Subscription[]>("/rbac-monetization/subscriptions", { params });
  return data;
}

export async function getSubscription(id: string): Promise<Subscription> {
  const { data } = await api.get<Subscription>(`/rbac-monetization/subscriptions/${id}`);
  return data;
}

// ---------------------------------------------------------------------------
// Subscription Lifecycle
// ---------------------------------------------------------------------------

export interface UpgradeDowngradePayload {
  new_plan_code: string;
  billing_period?: string;
  effective_immediately?: boolean;
}

export async function upgradeSubscription(
  id: string,
  payload: UpgradeDowngradePayload,
  options?: { stepUpToken?: string }
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(
    `/rbac-monetization/subscriptions/${id}/upgrade`,
    payload,
    { headers: options?.stepUpToken ? { "X-Step-Up-Token": options.stepUpToken } : undefined }
  );
  return data;
}

export async function downgradeSubscription(
  id: string,
  payload: UpgradeDowngradePayload,
  options?: { stepUpToken?: string }
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(
    `/rbac-monetization/subscriptions/${id}/downgrade`,
    payload,
    { headers: options?.stepUpToken ? { "X-Step-Up-Token": options.stepUpToken } : undefined }
  );
  return data;
}

export async function cancelSubscription(
  id: string,
  payload?: { reason?: string; immediate?: boolean },
  options?: { stepUpToken?: string }
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(
    `/rbac-monetization/subscriptions/${id}/cancel`,
    payload || {},
    { headers: options?.stepUpToken ? { "X-Step-Up-Token": options.stepUpToken } : undefined }
  );
  return data;
}

export async function reactivateSubscription(
  id: string,
  options?: { stepUpToken?: string }
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(
    `/rbac-monetization/subscriptions/${id}/reactivate`,
    {},
    { headers: options?.stepUpToken ? { "X-Step-Up-Token": options.stepUpToken } : undefined }
  );
  return data;
}

export async function changeBillingPeriod(
  id: string,
  newPeriod: BillingPeriod,
  options?: { stepUpToken?: string }
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(
    `/rbac-monetization/subscriptions/${id}/change-period`,
    { new_billing_period: newPeriod },
    { headers: options?.stepUpToken ? { "X-Step-Up-Token": options.stepUpToken } : undefined }
  );
  return data;
}

// ---------------------------------------------------------------------------
// Subscription Add-Ons
// ---------------------------------------------------------------------------

export async function addSubscriptionAddon(
  id: string,
  addonCode: string,
  options?: { stepUpToken?: string }
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(
    `/rbac-monetization/subscriptions/${id}/add-ons/add`,
    { add_on_code: addonCode },
    { headers: options?.stepUpToken ? { "X-Step-Up-Token": options.stepUpToken } : undefined }
  );
  return data;
}

export async function removeSubscriptionAddon(
  id: string,
  addonCode: string,
  options?: { stepUpToken?: string }
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(
    `/rbac-monetization/subscriptions/${id}/add-ons/remove`,
    { add_on_code: addonCode },
    { headers: options?.stepUpToken ? { "X-Step-Up-Token": options.stepUpToken } : undefined }
  );
  return data;
}

// ---------------------------------------------------------------------------
// History & Invoices
// ---------------------------------------------------------------------------

export interface SubscriptionHistoryEntry {
  id: string;
  subscription_id: string;
  organization_id: string;
  project_id?: string | null;
  change_type: string;
  from_plan_code?: string | null;
  to_plan_code?: string | null;
  from_status?: string | null;
  to_status?: string | null;
  from_billing_period?: string | null;
  to_billing_period?: string | null;
  proration_amount_minor?: number | null;
  add_on_code?: string | null;
  metadata?: Record<string, unknown>;
  changed_by?: string | null;
  changed_at: string;
}

export async function getSubscriptionHistory(id: string): Promise<SubscriptionHistoryEntry[]> {
  const { data } = await api.get<SubscriptionHistoryEntry[]>(
    `/rbac-monetization/subscriptions/${id}/history`
  );
  return data;
}

export interface InvoiceLineItem {
  description: string;
  amount_minor: number;
  code?: string;
}

export interface InvoicePreview {
  subscription_id: string;
  plan_code: string;
  plan_name: string;
  billing_period: string;
  line_items: InvoiceLineItem[];
  subtotal_minor: number;
  currency: string;
  period_start?: string | null;
  period_end?: string | null;
}

export async function getInvoicePreview(id: string): Promise<InvoicePreview> {
  const { data } = await api.get<InvoicePreview>(
    `/rbac-monetization/subscriptions/${id}/invoice-preview`
  );
  return data;
}

// ---------------------------------------------------------------------------
// Trial Management
// ---------------------------------------------------------------------------

export interface StartTrialPayload {
  organization_id: string;
  project_id?: string | null;
  plan_code: string;
  trial_days?: number;
}

export async function startTrial(
  payload: StartTrialPayload,
  options?: { stepUpToken?: string }
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(
    "/rbac-monetization/subscriptions/start-trial",
    payload,
    { headers: options?.stepUpToken ? { "X-Step-Up-Token": options.stepUpToken } : undefined }
  );
  return data;
}

export interface ConvertTrialPayload {
  billing_period: string;
  add_on_codes?: string[];
}

export async function convertTrial(
  id: string,
  payload: ConvertTrialPayload,
  options?: { stepUpToken?: string }
): Promise<Subscription> {
  const { data } = await api.post<Subscription>(
    `/rbac-monetization/subscriptions/${id}/convert-trial`,
    payload,
    { headers: options?.stepUpToken ? { "X-Step-Up-Token": options.stepUpToken } : undefined }
  );
  return data;
}

// ---------------------------------------------------------------------------
// Billing Summary
// ---------------------------------------------------------------------------

export interface BillingSummary {
  organization_id: string;
  total_subscriptions: number;
  active_subscriptions: number;
  subscriptions: Subscription[];
  current_period_usage: Record<string, number>;
  period_start: string;
}

export async function getBillingSummary(orgId: string): Promise<BillingSummary> {
  const { data } = await api.get<BillingSummary>(
    `/rbac-monetization/billing/summary/${orgId}`
  );
  return data;
}

export async function getOrganizationBillingHistory(
  orgId: string
): Promise<SubscriptionHistoryEntry[]> {
  const { data } = await api.get<SubscriptionHistoryEntry[]>(
    `/rbac-monetization/billing/history/${orgId}`
  );
  return data;
}
