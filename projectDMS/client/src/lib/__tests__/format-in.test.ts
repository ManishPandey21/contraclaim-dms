import { describe, expect, it } from "vitest";
import {
  formatCrore,
  formatDateIN,
  formatINR,
  fromDateInput,
  groupIndian,
  toDateInput,
} from "../format-in";

describe("Indian number grouping", () => {
  it("groups the last three digits, then pairs", () => {
    expect(groupIndian(184500000)).toBe("18,45,00,000");
    expect(groupIndian(100000)).toBe("1,00,000");
    expect(groupIndian(99999)).toBe("99,999");
    expect(groupIndian(999)).toBe("999");
    expect(groupIndian(0)).toBe("0");
  });

  it("keeps paise only when there are any, and handles negatives", () => {
    expect(groupIndian(1234567.5)).toBe("12,34,567.50");
    expect(groupIndian(-4500000)).toBe("-45,00,000");
  });
});

describe("formatINR", () => {
  it("prefixes the rupee sign", () => {
    expect(formatINR(184500000)).toBe("₹18,45,00,000");
    expect(formatINR(3100000, "INR")).toBe("₹31,00,000");
  });

  it("names a currency it has no symbol for and never invents a value", () => {
    expect(formatINR(1000, "AED")).toBe("AED 1,000");
    expect(formatINR(null)).toBe("—");
    expect(formatINR(undefined)).toBe("—");
    expect(formatINR(Number.NaN)).toBe("—");
  });

  it("shows zero as zero, not as missing", () => {
    expect(formatINR(0)).toBe("₹0");
  });
});

describe("formatCrore", () => {
  it("shortens to crore and lakh", () => {
    expect(formatCrore(184500000)).toBe("₹18.45 Cr");
    expect(formatCrore(45000000)).toBe("₹4.5 Cr");
    expect(formatCrore(3100000)).toBe("₹31 L");
    expect(formatCrore(99999)).toBe("₹99,999");
    expect(formatCrore(-92000000)).toBe("-₹9.2 Cr");
  });

  it("does not apply crore to another currency", () => {
    expect(formatCrore(20000000, "USD")).toBe("$2,00,00,000");
    expect(formatCrore(null)).toBe("—");
  });
});

describe("dates", () => {
  it("prints DD-MM-YYYY from the date part as written", () => {
    expect(formatDateIN("2026-11-20")).toBe("20-11-2026");
    expect(formatDateIN("2026-11-20T00:00:00Z")).toBe("20-11-2026");
    // Late-evening UTC must not roll over to the next day for a viewer in IST.
    expect(formatDateIN("2026-03-30T23:59:00Z")).toBe("30-03-2026");
  });

  it("returns a dash for nothing or for something that is not a date", () => {
    expect(formatDateIN(null)).toBe("—");
    expect(formatDateIN("")).toBe("—");
    expect(formatDateIN("soon")).toBe("—");
  });

  it("round-trips through a date input", () => {
    expect(toDateInput("2026-03-02T00:00:00Z")).toBe("2026-03-02");
    expect(toDateInput(null)).toBe("");
    expect(fromDateInput("2026-03-02")).toBe("2026-03-02T00:00:00Z");
    expect(fromDateInput("")).toBeUndefined();
    expect(fromDateInput("02/03/2026")).toBeUndefined();
  });
});
