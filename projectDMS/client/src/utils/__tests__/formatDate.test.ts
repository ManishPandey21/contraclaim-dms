import { describe, expect, it } from "vitest";
import { formatDate, formatDateTime } from "../formatDate";

describe("formatDate", () => {
  it("renders day-first regardless of the viewer's locale", () => {
    // The trap this replaces: toLocaleDateString() renders this as 15/04/2026
    // in London and 4/15/2026 in New York, for the same contractual date.
    expect(formatDate("2026-04-15T00:00:00")).toBe("15-04-2026");
  });

  it("zero-pads single-digit days and months", () => {
    expect(formatDate("2026-01-02T00:00:00")).toBe("02-01-2026");
  });

  it("accepts a Date as well as a string", () => {
    expect(formatDate(new Date(2026, 3, 15))).toBe("15-04-2026");
  });

  it("falls back for empty and unparseable values", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate(undefined)).toBe("—");
    expect(formatDate("")).toBe("—");
    expect(formatDate("not a date")).toBe("—");
    expect(formatDate(null, "")).toBe("");
  });

  it("renders day-first date and 24h time together", () => {
    expect(formatDateTime(new Date(2026, 3, 15, 9, 5))).toBe("15-04-2026 09:05");
  });
});
