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

// --- Pending-checkout continuity --------------------------------------------
// Razorpay subscription links have no callback_url parameter: after paying on
// the hosted page the customer is NOT redirected back to the app. We remember
// the in-flight checkout before leaving; the next visit to the subscription
// page routes through /billing/return so the user sees payment confirmation
// (webhook-driven) instead of a stale page.

const PENDING_CHECKOUT_KEY = "cc_pending_checkout";
const PENDING_CHECKOUT_TTL_MS = 24 * 60 * 60 * 1000;

export function rememberPendingCheckout(subscriptionId: string): void {
  try {
    localStorage.setItem(
      PENDING_CHECKOUT_KEY,
      JSON.stringify({ subscriptionId, at: Date.now() })
    );
  } catch {
    // Storage unavailable (private mode): the webhook still activates the
    // subscription; the user just lands on the normal subscription page.
  }
}

export function peekPendingCheckout(): string | null {
  try {
    const raw = localStorage.getItem(PENDING_CHECKOUT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { subscriptionId?: string; at?: number };
    if (!parsed?.subscriptionId || Date.now() - (parsed.at ?? 0) > PENDING_CHECKOUT_TTL_MS) {
      localStorage.removeItem(PENDING_CHECKOUT_KEY);
      return null;
    }
    return String(parsed.subscriptionId);
  } catch {
    return null;
  }
}

export function clearPendingCheckout(): void {
  try {
    localStorage.removeItem(PENDING_CHECKOUT_KEY);
  } catch {
    // ignore
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
  // Present on review-queue items (enriched from the subscription).
  plan_code?: string | null;
  subscription_billing_status?: string | null;
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

/** Failed-payment / amount-mismatch records needing admin attention. */
export async function getBillingReviewQueue(organizationId: string): Promise<BillingRecord[]> {
  const { data } = await api.get(`/rbac-monetization/billing/review-queue/${organizationId}`);
  return Array.isArray(data) ? data.map(normBillingRecord) : [];
}

/** Download the receipt / GST tax invoice (HTML, print-to-PDF) for a paid record. */
export async function downloadBillingReceipt(
  recordId: string,
  organizationId: string,
  format: "receipt" | "tax_invoice" = "receipt",
): Promise<void> {
  const { data } = await api.get(`/rbac-monetization/billing/records/${recordId}/receipt`, {
    params: { organization_id: organizationId, format },
    responseType: "blob",
  });
  const blob = data instanceof Blob ? data : new Blob([data], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${format === "tax_invoice" ? "tax-invoice" : "receipt"}-${recordId}.html`;
  a.click();
  URL.revokeObjectURL(url);
}
