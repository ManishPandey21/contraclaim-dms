import { describe, it, expect } from "vitest";
import { buildSlaStateMap, followUpTaskTitle } from "../claims-helpers";
import type { SlaItem } from "@/services/sla-api";

const item = (over: Partial<SlaItem>): SlaItem => ({
  claim_id: "c1",
  kind: "response",
  days_remaining: 5,
  state: "approaching",
  ...over,
});

describe("buildSlaStateMap (Phase 2)", () => {
  it("keeps the most urgent item per claim (breached outranks approaching)", () => {
    const map = buildSlaStateMap([
      item({ claim_id: "c1", state: "approaching", days_remaining: 3 }),
      item({ claim_id: "c1", state: "breached", days_remaining: -2, kind: "time_bar" }),
    ]);
    expect(map.c1.state).toBe("breached");
    expect(map.c1.days_remaining).toBe(-2);
  });

  it("within the same state, the nearer deadline wins", () => {
    const map = buildSlaStateMap([
      item({ claim_id: "c2", state: "approaching", days_remaining: 9 }),
      item({ claim_id: "c2", state: "approaching", days_remaining: 2 }),
    ]);
    expect(map.c2.days_remaining).toBe(2);
  });

  it("maps each claim independently", () => {
    const map = buildSlaStateMap([
      item({ claim_id: "a", state: "breached", days_remaining: -1 }),
      item({ claim_id: "b", state: "approaching", days_remaining: 4 }),
    ]);
    expect(map.a.state).toBe("breached");
    expect(map.b.state).toBe("approaching");
  });
});

describe("followUpTaskTitle", () => {
  it("prefers the claim ref, falls back to title", () => {
    expect(followUpTaskTitle({ claim_ref: "EOT-001", title: "Monsoon" })).toBe(
      "Follow up: EOT-001",
    );
    expect(followUpTaskTitle({ claim_ref: null, title: "Monsoon delay" })).toBe(
      "Follow up: Monsoon delay",
    );
  });
});
