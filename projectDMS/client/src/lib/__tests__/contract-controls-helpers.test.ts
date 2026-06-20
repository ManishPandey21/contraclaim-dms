import { describe, it, expect } from "vitest";
import {
  variationStatusColor,
  variationStatusLabel,
  bgStatusColor,
  bgStatusLabel,
  bgAlertText,
  fmtAmount,
} from "../contract-controls-helpers";

describe("contract-controls-helpers", () => {
  it("colours variation statuses", () => {
    expect(variationStatusColor("approved")).toContain("green");
    expect(variationStatusColor("rejected")).toContain("red");
    expect(variationStatusLabel("under_review")).toBe("Under Review");
  });

  it("colours BG statuses", () => {
    expect(bgStatusColor("valid")).toContain("green");
    expect(bgStatusColor("expired")).toContain("red");
    expect(bgStatusColor("extension_required")).toContain("orange");
    expect(bgStatusColor("extended")).toContain("purple");
    expect(bgStatusLabel("extension_required")).toBe("Extension Required");
  });

  it("renders BG alert text", () => {
    expect(bgAlertText({ days_to_expiry: -5, extension_required: true, bg_status: "valid" })).toBe("Expired 5d");
    expect(bgAlertText({ days_to_expiry: 30, extension_required: true, bg_status: "valid" })).toBe("30d (extend)");
    expect(bgAlertText({ days_to_expiry: 120, extension_required: false, bg_status: "valid" })).toBe("OK");
    expect(bgAlertText({ days_to_expiry: 10, extension_required: true, bg_status: "released" })).toBe("—");
  });

  it("formats amounts", () => {
    expect(fmtAmount(null)).toBe("—");
    // Locale-agnostic: prefixed with the currency and grouped (not the raw number).
    const formatted = fmtAmount(1000000);
    expect(formatted).toContain("INR");
    expect(formatted).toContain(",");
    expect(formatted).not.toContain("1000000");
  });
});
