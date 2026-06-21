import { api } from "./api";

// Admin catalog CRUD for the monetization plans/add-ons/expert-allocations.
// Mutations are step-up gated server-side (billing.plan.manage). This module
// covers list + update (activate/deactivate + core-field edit); nested-JSON
// create is intentionally out of scope here.

export interface AdminPlan {
  id: string;
  code: string;
  name: string;
  description?: string | null;
  family?: string;
  tier?: number;
  billing_cadence?: string;
  currency?: string;
  base_price_minor?: number;
  is_active?: boolean;
  display_order?: number;
  highlight?: boolean;
  max_users?: number | null;
  max_storage_gb?: number | null;
  pricing_tiers?: Record<string, number>;
  discount_percentages?: Record<string, number>;
  features?: Record<string, unknown>;
  default_limits?: Record<string, unknown>;
  available_add_ons?: string[];
  trial_config?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface AdminAddOn {
  id: string;
  code: string;
  name: string;
  description?: string | null;
  add_on_type?: string;
  price_minor?: number;
  billing_cadence?: string;
  currency?: string;
  is_active?: boolean;
  features?: Record<string, unknown>;
  limits?: Record<string, unknown>;
  compatible_plans?: string[];
  [k: string]: unknown;
}

export interface AdminExpertAllocation {
  id: string;
  assignment_role?: string;
  status?: string;
  package_id?: string | null;
  drafting_request_id?: string | null;
  letter_id?: string | null;
  allocation_reason?: string | null;
  work_order_reference?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  [k: string]: unknown;
}

const norm = <T,>(raw: any): T => ({ ...raw, id: raw?._id ?? raw?.id }) as T;
const stepHeaders = (t?: string) =>
  t ? { "X-Step-Up-Token": t } : undefined;

export async function listPlans(): Promise<AdminPlan[]> {
  const { data } = await api.get("/rbac-monetization/plans");
  return Array.isArray(data) ? data.map((d) => norm<AdminPlan>(d)) : [];
}

export async function updatePlan(
  id: string,
  payload: Record<string, unknown>,
  opt?: { stepUpToken?: string },
): Promise<AdminPlan> {
  const { data } = await api.put(`/rbac-monetization/plans/${id}`, payload, {
    headers: stepHeaders(opt?.stepUpToken),
  });
  return norm<AdminPlan>(data);
}

export async function listAddOns(): Promise<AdminAddOn[]> {
  const { data } = await api.get("/rbac-monetization/add-ons");
  return Array.isArray(data) ? data.map((d) => norm<AdminAddOn>(d)) : [];
}

export async function updateAddOn(
  id: string,
  payload: Record<string, unknown>,
  opt?: { stepUpToken?: string },
): Promise<AdminAddOn> {
  const { data } = await api.put(`/rbac-monetization/add-ons/${id}`, payload, {
    headers: stepHeaders(opt?.stepUpToken),
  });
  return norm<AdminAddOn>(data);
}

export async function listExpertAllocations(): Promise<AdminExpertAllocation[]> {
  const { data } = await api.get("/rbac-monetization/expert-allocations");
  return Array.isArray(data)
    ? data.map((d) => norm<AdminExpertAllocation>(d))
    : [];
}

export async function updateExpertAllocation(
  id: string,
  payload: Record<string, unknown>,
  opt?: { stepUpToken?: string },
): Promise<AdminExpertAllocation> {
  const { data } = await api.put(
    `/rbac-monetization/expert-allocations/${id}`,
    payload,
    { headers: stepHeaders(opt?.stepUpToken) },
  );
  return norm<AdminExpertAllocation>(data);
}
