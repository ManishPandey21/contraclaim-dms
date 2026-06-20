import { describe, it, expect } from "vitest";
import {
  subscriptionDisplayState,
  shouldKeepPolling,
  formatMinorAmount,
  billingRecordStatusColor,
  billingRecordStatusLabel,
} from "../billing-helpers";

describe("billing-helpers (Phase 4)", () => {
  it("maps active-like statuses to active", () => {
    for (const s of ["active", "trial", "pilot", "ACTIVE"]) {
      expect(subscriptionDisplayState(s).state).toBe("active");
    }
  });

  it("maps in-flight statuses to pending", () => {
    for (const s of ["pending", "created", "authenticated", "pending_payment"]) {
      expect(subscriptionDisplayState(s).state).toBe("pending");
    }
  });

  it("maps terminal/failed statuses to failed", () => {
    for (const s of ["halted", "cancelled", "failed", "expired", "past_due"]) {
      expect(subscriptionDisplayState(s).state).toBe("failed");
    }
  });

  it("treats unknown/empty as unknown", () => {
    expect(subscriptionDisplayState(undefined).state).toBe("unknown");
    expect(subscriptionDisplayState("").state).toBe("unknown");
  });

  it("keeps polling only while pending or unknown", () => {
    expect(shouldKeepPolling("pending")).toBe(true);
    expect(shouldKeepPolling("unknown")).toBe(true);
    expect(shouldKeepPolling("active")).toBe(false);
    expect(shouldKeepPolling("failed")).toBe(false);
  });

  it("formats minor-unit amounts as major-unit currency", () => {
    expect(formatMinorAmount(50000, "INR")).toBe("INR 500.00");
    expect(formatMinorAmount(4999, "INR")).toBe("INR 49.99");
    expect(formatMinorAmount(null)).toBe("—");
  });

  it("labels + colours billing-record statuses (mismatch flagged)", () => {
    expect(billingRecordStatusLabel("amount_mismatch")).toBe("Amount mismatch — review");
    expect(billingRecordStatusLabel("paid")).toBe("Paid");
    expect(billingRecordStatusColor("paid")).toContain("green");
    expect(billingRecordStatusColor("failed")).toContain("red");
    expect(billingRecordStatusColor("amount_mismatch")).toContain("amber");
    expect(billingRecordStatusColor("whatever")).toContain("gray");
  });
});
