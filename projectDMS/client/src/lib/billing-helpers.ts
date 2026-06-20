// Pure helpers for the billing return/status screen (Phase 4).
// The backend (via signed Razorpay webhooks) is the source of truth for whether
// a subscription is active; the UI only maps the polled status to a display.

export type CheckoutDisplayState = "active" | "pending" | "failed" | "unknown";

const ACTIVE = new Set(["active", "trial", "pilot"]);
const PENDING = new Set(["pending", "created", "authenticated", "pending_payment"]);
const FAILED = new Set(["halted", "cancelled", "canceled", "failed", "expired", "past_due"]);

export function subscriptionDisplayState(status?: string | null): {
  state: CheckoutDisplayState;
  label: string;
} {
  const s = String(status || "").toLowerCase();
  if (ACTIVE.has(s)) return { state: "active", label: "Active" };
  if (PENDING.has(s)) return { state: "pending", label: "Payment processing" };
  if (FAILED.has(s)) return { state: "failed", label: "Payment failed or cancelled" };
  return { state: "unknown", label: status || "Unknown" };
}

/** Whether the return screen should keep polling for a status change. */
export function shouldKeepPolling(state: CheckoutDisplayState): boolean {
  return state === "pending" || state === "unknown";
}

// --- Billing records (financial history) ----------------------------------

/** Format a minor-unit amount (e.g. paise) as a major-unit currency string. */
export function formatMinorAmount(amountMinor?: number | null, currency = "INR"): string {
  if (amountMinor == null) return "—";
  return `${currency} ${(amountMinor / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

const RECORD_STATUS_COLOR: Record<string, string> = {
  paid: "bg-green-600",
  failed: "bg-red-600",
  amount_mismatch: "bg-amber-600",
};

/** Badge colour for a billing-record status. */
export function billingRecordStatusColor(status?: string | null): string {
  return RECORD_STATUS_COLOR[String(status || "").toLowerCase()] || "bg-gray-500";
}

/** Human label for a billing-record status. */
export function billingRecordStatusLabel(status?: string | null): string {
  const s = String(status || "").toLowerCase();
  if (s === "amount_mismatch") return "Amount mismatch — review";
  if (s === "paid") return "Paid";
  if (s === "failed") return "Failed";
  return status ? String(status).replace(/_/g, " ") : "—";
}
