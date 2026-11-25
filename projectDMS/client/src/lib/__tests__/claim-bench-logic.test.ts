import { describe, expect, it } from "vitest";
import {
  differsFromProposal,
  headProgress,
  lightDescription,
  lightTone,
  parseAmount,
  parseDays,
  proposalSource,
  testSummary,
} from "../claim-bench-logic";
import { ELEMENT_KEYS, type ClaimHead, type HeadElement } from "@/types/claimBench";

const element = (over: Partial<HeadElement> = {}): HeadElement => ({ source_ids: [], ...over });

const head = (over: Partial<ClaimHead> = {}): ClaimHead => ({
  head_id: "H1",
  title: "Site handover",
  kind: "time_and_cost",
  clause_refs: [],
  elements: Object.fromEntries(ELEMENT_KEYS.map((key) => [key, element()])) as ClaimHead["elements"],
  residual_risks: [],
  missing_information: [],
  test_stale: false,
  ...over,
});

describe("lights", () => {
  it("shows a proposal as proposed, never as settled", () => {
    expect(lightTone(element({ proposed_state: "supported" }))).toBe("proposed");
    expect(lightDescription("notice", element({ proposed_state: "supported" }))).toBe(
      "Notice: proposed supported, not yet decided",
    );
  });

  it("shows the person's decision once made, whatever was proposed", () => {
    expect(lightTone(element({ proposed_state: "supported", state: "weak" }))).toBe("weak");
    expect(lightTone(element({ state: "not_applicable" }))).toBe("na");
    expect(lightDescription("quantum", element({ state: "missing" }))).toBe("Quantum: decided missing");
  });

  it("is empty before any test", () => {
    expect(lightTone(element())).toBe("empty");
    expect(lightTone(undefined)).toBe("empty");
    expect(lightDescription("event", element())).toBe("Event: not tested");
  });
});

describe("head progress", () => {
  it("is done only when all five elements and the classification are decided", () => {
    const decided = Object.fromEntries(ELEMENT_KEYS.map((key) => [key, element({ state: "weak" })])) as ClaimHead["elements"];
    expect(headProgress(head())).toEqual({ decided: 0, total: 5, done: false });
    expect(headProgress(head({ elements: decided })).done).toBe(false);
    expect(headProgress(head({ elements: decided, classification: "excusable" }))).toEqual({
      decided: 5,
      total: 5,
      done: true,
    });
  });

  it("does not count proposals as progress", () => {
    const proposed = Object.fromEntries(
      ELEMENT_KEYS.map((key) => [key, element({ proposed_state: "supported" })]),
    ) as ClaimHead["elements"];
    expect(headProgress(head({ elements: proposed, proposed_classification: "compensable" })).decided).toBe(0);
  });
});

describe("proposals", () => {
  it("flags a decision that differs from the proposal", () => {
    expect(differsFromProposal(element({ proposed_state: "weak", state: "supported" }))).toBe(true);
    expect(differsFromProposal(element({ proposed_state: "weak", state: "weak" }))).toBe(false);
    expect(differsFromProposal(element({ state: "weak" }))).toBe(false);
  });

  it("says whether the skill or the records made the proposal", () => {
    expect(proposalSource(element({ proposed_state: "weak", proposed_by: "skill" }))).toBe("Proposed by the claims skill");
    expect(proposalSource(element({ proposed_state: "weak", proposed_by: "record_check" }))).toBe("From the records only");
    expect(proposalSource(element())).toBe("");
  });

  it("summarises the last test, including when it is out of date", () => {
    expect(testSummary(head())).toBe("Not tested yet");
    const records = head({ step_id: "s1", elements: { ...head().elements, event: element({ proposed_state: "weak", proposed_by: "record_check" }) } });
    expect(testSummary(records)).toBe("Checked against the records only");
    const skill = head({ step_id: "s1", test_stale: true, elements: { ...head().elements, event: element({ proposed_state: "weak", proposed_by: "skill" }) } });
    expect(testSummary(skill)).toBe("Tested with the claims skill, before the head was last edited");
  });
});

describe("number entry", () => {
  it("reads Indian-grouped amounts and treats empty as cleared", () => {
    expect(parseAmount("18,45,00,000")).toBe(184500000);
    expect(parseAmount("₹ 1,000.50")).toBe(1000.5);
    expect(parseAmount("")).toBeNull();
    expect(parseAmount("-5")).toBeUndefined();
    expect(parseAmount("lots")).toBeUndefined();
  });

  it("reads whole days only", () => {
    expect(parseDays("146")).toBe(146);
    expect(parseDays(" ")).toBeNull();
    expect(parseDays("14.5")).toBeUndefined();
    expect(parseDays("-3")).toBeUndefined();
  });
});
