import { describe, expect, it } from "vitest";

import {
  ARBITRATION_REVIEWER_ROLES,
  REVIEWER_ROLE_LABELS,
  SKILL_REVIEWER_ROLES,
  defaultExpiry,
  reviewerRolesForScope,
  rolesAfterEdit,
} from "../reviewer-roles";

describe("rolesAfterEdit (4B-08: the Users form must not strip a user's other roles)", () => {
  it("replaces the access role and keeps every other role in order", () => {
    expect(rolesAfterEdit(["projectuser", "quantum_reviewer", "panel_counsel"], "projectadmin")).toEqual([
      "projectadmin",
      "quantum_reviewer",
      "panel_counsel",
    ]);
  });

  it("keeps the list unchanged when the access role is unchanged", () => {
    expect(rolesAfterEdit(["orgadmin", "senior_legal_approver"], "orgadmin")).toEqual([
      "orgadmin",
      "senior_legal_approver",
    ]);
  });

  it("does not duplicate a role the user already held further down the list", () => {
    expect(rolesAfterEdit(["projectuser", "orguser", "orguser", "legal_reviewer"], "orguser")).toEqual([
      "orguser",
      "legal_reviewer",
    ]);
  });

  it("gives a one-item list for a user with one role, none, or no list at all", () => {
    expect(rolesAfterEdit(["projectuser"], "orguser")).toEqual(["orguser"]);
    expect(rolesAfterEdit([], "orguser")).toEqual(["orguser"]);
    expect(rolesAfterEdit(undefined, "orguser")).toEqual(["orguser"]);
  });
});

describe("reviewer roles by scope", () => {
  it("offers only skill reviewer roles at platform scope", () => {
    expect(reviewerRolesForScope("platform")).toEqual([...SKILL_REVIEWER_ROLES]);
  });

  it("offers every reviewer role at organisation and project scope", () => {
    const all = [...SKILL_REVIEWER_ROLES, ...ARBITRATION_REVIEWER_ROLES];
    expect(reviewerRolesForScope("organization")).toEqual(all);
    expect(reviewerRolesForScope("project")).toEqual(all);
  });

  it("has a plain label for every role", () => {
    for (const role of [...SKILL_REVIEWER_ROLES, ...ARBITRATION_REVIEWER_ROLES]) {
      expect(REVIEWER_ROLE_LABELS[role]).toBeTruthy();
      expect(REVIEWER_ROLE_LABELS[role]).not.toContain("_");
    }
  });
});

describe("defaultExpiry", () => {
  it("is one day short of twelve months, so it is within the server's limit", () => {
    expect(defaultExpiry(new Date("2026-11-17T10:00:00Z"))).toBe("2027-11-16");
    expect(defaultExpiry(new Date("2028-02-29T00:00:00Z"))).toBe("2029-02-28");
  });
});
