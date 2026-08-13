import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  formatDate,
  formatDateTime,
  getAvailableDateFormats,
  getDateFormatLabel,
  getUserDateFormat,
  setUserDateFormat,
} from "../dateFormat";

const APRIL_15 = "2026-04-15T00:00:00";

// The shared test setup replaces localStorage with vi.fn() stubs, so a stored
// preference has to be driven through the mock rather than actually written.
const storedFormat = (value: string | null) =>
  vi.mocked(localStorage.getItem).mockReturnValue(value);

describe("dateFormat", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    storedFormat(null);
  });

  it("defaults to dd-mm-yyyy when the user has no stored preference", () => {
    expect(getUserDateFormat()).toBe("dd-mm-yyyy");
    expect(formatDate(APRIL_15)).toBe("15-04-2026");
  });

  it("falls back to dd-mm-yyyy when the stored preference is unrecognised", () => {
    storedFormat("not-a-format");
    expect(getUserDateFormat()).toBe("dd-mm-yyyy");
    expect(formatDate(APRIL_15)).toBe("15-04-2026");
  });

  it("still honours every previously supported format", () => {
    expect(formatDate(APRIL_15, "dd/mm/yyyy")).toBe("15/04/2026");
    expect(formatDate(APRIL_15, "mm/dd/yyyy")).toBe("04/15/2026");
    expect(formatDate(APRIL_15, "yyyy-mm-dd")).toBe("2026-04-15");
  });

  it("respects a stored preference over the new default", () => {
    storedFormat("mm/dd/yyyy");
    expect(getUserDateFormat()).toBe("mm/dd/yyyy");
    expect(formatDate(APRIL_15)).toBe("04/15/2026");
  });

  it("persists a chosen format under the dateFormat key", () => {
    setUserDateFormat("dd-mm-yyyy");
    expect(localStorage.setItem).toHaveBeenCalledWith("dateFormat", "dd-mm-yyyy");
  });

  it("zero-pads single-digit days and months", () => {
    expect(formatDate("2026-01-02T00:00:00", "dd-mm-yyyy")).toBe("02-01-2026");
  });

  it("offers dd-mm-yyyy in the selectable formats, listed first", () => {
    const formats = getAvailableDateFormats();
    expect(formats[0]).toBe("dd-mm-yyyy");
    expect(formats).toContain("dd/mm/yyyy");
  });

  it("labels the new format", () => {
    expect(getDateFormatLabel("dd-mm-yyyy")).toMatch(/DD-MM-YYYY/);
  });

  it("formats date and time together using the new default", () => {
    expect(formatDateTime(new Date(2026, 3, 15, 9, 5))).toBe("15-04-2026 09:05");
  });

  it("keeps its existing empty and invalid handling", () => {
    expect(formatDate(null)).toBe("N/A");
    expect(formatDate("not a date")).toBe("Invalid Date");
  });
});
