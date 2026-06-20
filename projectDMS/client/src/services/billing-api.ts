import { api } from "./api";

// ---------------------------------------------------------------------------
// Billing / checkout API (Week 3.4 scaffold)
//
// Wires the frontend to the backend Razorpay checkout + subscription flow.
// `startSubscriptionCheckout` provisions a pending subscription on the gateway
// and returns a `checkout_url` to redirect the customer to. The subscription is
// promoted to `active` by the backend billing webhook once payment is captured.
//
// NOTE: the checkout endpoint requires a step-up token (recent password
// re-auth). Obtain one via `POST /step-up` and pass it here.
// End-to-end verification requires live Razorpay keys.
// ---------------------------------------------------------------------------

export interface StartCheckoutPayload {
  organization_id: string;
  project_id?: string | null;
  plan_code: string;
  billing_period?: "monthly" | "quarterly" | "semi_annual" | "annual";
  customer_name?: string;
  customer_email?: string;
}

export interface CheckoutResponse {
  subscription_id: string;
  provider: string;
  gateway_subscription_id: string;
  checkout_url?: string | null;
  status: string;
}

/**
 * Start a subscription checkout. Returns the gateway checkout URL.
 * @param payload      organization/plan details
 * @param stepUpToken  short-lived step-up token from POST /step-up
 */
export async function startSubscriptionCheckout(
  payload: StartCheckoutPayload,
  stepUpToken: string
): Promise<CheckoutResponse> {
  const res = await api.post(
    "/rbac-monetization/subscriptions/checkout",
    payload,
    { headers: { "X-Step-Up-Token": stepUpToken } }
  );
  return res.data as CheckoutResponse;
}

/**
 * Redirect the browser to the gateway-hosted checkout page.
 */
export function redirectToCheckout(checkout: CheckoutResponse): void {
  if (checkout.checkout_url) {
    window.location.assign(checkout.checkout_url);
  }
}

// --- Billing records (financial history) ----------------------------------

export interface BillingRecord {
  id: string;
  subscription_id?: string | null;
  organization_id?: string | null;
  project_id?: string | null;
  provider?: string | null;
  event_type?: string | null;
  gateway_payment_id?: string | null;
  amount_minor?: number | null;
  currency?: string | null;
  record_status?: string | null; // paid | failed | amount_mismatch | <event_type>
  validation_error?: string | null;
  created_at?: string | null;
}

const normBillingRecord = (raw: any): BillingRecord => ({
  ...raw,
  id: raw?._id ?? raw?.id,
});

/** Financial billing records (payments / failures / amount-mismatch) for an org. */
export async function getBillingRecords(organizationId: string): Promise<BillingRecord[]> {
  const { data } = await api.get(`/rbac-monetization/billing/records/${organizationId}`);
  return Array.isArray(data) ? data.map(normBillingRecord) : [];
}
