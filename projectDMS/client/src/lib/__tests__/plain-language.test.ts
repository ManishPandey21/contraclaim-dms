import { describe, expect, it } from "vitest";

import { containsRawId, errorMessage, humanise, plainStatus } from "@/lib/plain-language";

describe("plain-language", () => {
  it("names statuses in plain words and never shows a raw key", () => {
    expect(plainStatus("arbitration_case", "intake")).toBe("Prepare the case file");
    expect(plainStatus("arbitration_case", "matrix_preparation")).toBe("Review the matrices");
    expect(plainStatus("arbitration_case", "ready_for_drafting")).toBe("Approved for drafting");
    expect(plainStatus("claim", "under_review")).toBe("Under review");
    expect(plainStatus("claim", "brand_new_state")).toBe("Brand new state");
    expect(plainStatus("claim", null)).toBe("Not set");
    expect(humanise("a.b_c")).toBe("A b c");
  });

  it("renders a structured refusal from user_message", () => {
    const error = { response: { status: 422, data: { detail: { code: "x", user_message: "Choose the notice letter first." } } } };
    expect(errorMessage(error)).toBe("Choose the notice letter first.");
  });

  it("renders a known code when there is no user_message", () => {
    expect(errorMessage({ response: { status: 403, data: { detail: { code: "explicit_grant_required" } } } })).toMatch(/granted to you by name/);
    expect(errorMessage({ response: { status: 403, data: { detail: "Not authorized: missing_permission" } } })).toBe(
      "You do not have permission to do this."
    );
  });

  it("scrubs ids and never shows stack text", () => {
    const id = "65f0a1b2c3d4e5f6a7b8c9d0";
    const message = errorMessage({ response: { status: 404, data: { detail: `Case ${id} not found` } } });
    expect(message).toBe("Case a record not found");
    expect(containsRawId(message)).toBe(false);
    const stack = errorMessage({ response: { status: 500, data: { detail: 'Traceback (most recent call last):\n  File "x.py", line 3' } } });
    expect(stack).not.toMatch(/Traceback|line 3/);
    expect(errorMessage(new Error("boom"))).toMatch(/Something went wrong/);
  });

  it("detects ObjectIds and UUIDs", () => {
    expect(containsRawId("id 65f0a1b2c3d4e5f6a7b8c9d0")).toBe(true);
    expect(containsRawId("ref 1b4e28ba-2fa1-11d2-883f-0016d3cca427")).toBe(true);
    expect(containsRawId("NHAI/PD/112 · Notice of delay")).toBe(false);
  });
});
