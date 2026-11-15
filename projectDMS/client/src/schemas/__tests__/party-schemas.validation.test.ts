import { zodResolver } from "@hookform/resolvers/zod";
import { describe, expect, it } from "vitest";

import {
  concernSchema,
  partySchema,
  representativeSchema,
  validateConcernForm,
  validatePartyForm,
  validateRepresentativeForm,
} from "../party-schemas";

// Pins the validation contract these forms had on Zod 3, so the Zod 4 upgrade
// (GHSA-mrhx-c7xc-m3w5) is proven not to change rules, defaults, optional/""
// handling, error messages or what react-hook-form receives from the resolver.

const validParty = { name: "Acme Infra", type: "Organization" as const };

function messages(result: { success: boolean; error?: { issues: { path: PropertyKey[]; message: string }[] } }) {
  return Object.fromEntries((result.error?.issues ?? []).map((issue) => [issue.path.join("."), issue.message]));
}

async function resolve(schema: Parameters<typeof zodResolver>[0], values: Record<string, unknown>) {
  const resolver = zodResolver(schema);
  return resolver(values, undefined, { fields: {}, shouldUseNativeValidation: false });
}

describe("party schemas keep their Zod 3 validation contract", () => {
  it("applies defaults and accepts empty optional strings", () => {
    const result = validatePartyForm({ ...validParty, contactEmail: "", contactPhone: "" });
    expect(result.success).toBe(true);
    expect(result.data?.projects).toEqual([]);
    expect(result.data?.contactEmail).toBe("");

    const representative = validateRepresentativeForm({ name: "Ravi", email: "ravi@example.com" });
    expect(representative.success).toBe(true);
    expect(representative.data?.isPrimary).toBe(false);
    expect(representative.data?.phone).toBeUndefined();
  });

  it("keeps an omitted optional field undefined rather than defaulted", () => {
    const result = validatePartyForm(validParty);
    expect(result.success).toBe(true);
    expect(result.data).not.toHaveProperty("contactEmail");
    expect(result.data?.projects).toEqual([]);
  });

  it("reports the same custom messages", () => {
    expect(
      messages(
        validatePartyForm({
          name: "A",
          type: "Organization",
          contactEmail: "not-an-email",
          contactPhone: "123",
        }),
      ),
    ).toEqual({
      name: "Name must be at least 2 characters",
      contactEmail: "Invalid email address",
      contactPhone: "Phone number must be at least 10 digits",
    });

    expect(
      messages(validateRepresentativeForm({ name: "R", email: "x", position: "p".repeat(101) })),
    ).toEqual({
      name: "Name must be at least 2 characters",
      email: "Invalid email address",
      position: "Position cannot exceed 100 characters",
    });

    expect(messages(validateConcernForm({ name: "Delay", partyId: "", emails: [] }))).toEqual({
      partyId: "Party must be selected",
      emails: "At least one email address is required",
    });
    expect(messages(validateConcernForm({ name: "Delay", partyId: "p1", emails: ["bad"] }))).toEqual({
      "emails.0": "Invalid email address",
    });
  });

  it("accepts and rejects the same email addresses", () => {
    const accepted = ["ravi@example.com", "o'brien@example.co.in", "a.b+tag@sub.example.org", "UPPER@EXAMPLE.COM"];
    const rejected = ["plain", "a@b", "a..b@example.com", ".a@example.com", "a@example", "a b@example.com"];
    for (const email of accepted) {
      expect(representativeSchema.safeParse({ name: "Ravi", email }).success, email).toBe(true);
    }
    for (const email of rejected) {
      expect(representativeSchema.safeParse({ name: "Ravi", email }).success, email).toBe(false);
    }
  });

  it("enforces the same length bounds at the edges", () => {
    expect(partySchema.safeParse({ ...validParty, name: "x".repeat(100) }).success).toBe(true);
    expect(partySchema.safeParse({ ...validParty, name: "x".repeat(101) }).success).toBe(false);
    expect(concernSchema.safeParse({ name: "Delay", partyId: "p", description: "d".repeat(500), emails: ["a@b.co"] }).success).toBe(true);
    expect(concernSchema.safeParse({ name: "Delay", partyId: "p", description: "d".repeat(501), emails: ["a@b.co"] }).success).toBe(false);
  });
});

describe("zodResolver hands react-hook-form the same shape", () => {
  it("returns parsed values with defaults and no errors for valid input", async () => {
    const result = await resolve(representativeSchema, { name: "Ravi", email: "ravi@example.com", phone: "" });
    expect(result.errors).toEqual({});
    expect(result.values).toEqual({ name: "Ravi", email: "ravi@example.com", phone: "", isPrimary: false });
  });

  it("returns one message per field, keyed by path, and no values", async () => {
    const result = await resolve(partySchema, { name: "A", type: "Organization", contactEmail: "bad" });
    expect(result.values).toEqual({});
    expect(result.errors.name).toMatchObject({ message: "Name must be at least 2 characters", type: "too_small" });
    expect(result.errors.contactEmail).toMatchObject({ message: "Invalid email address" });
    expect(Object.keys(result.errors).sort()).toEqual(["contactEmail", "name"]);
  });

  it("nests array element errors under their index", async () => {
    const result = await resolve(concernSchema, { name: "Delay", partyId: "p1", emails: ["ok@example.com", "bad"] });
    const emails = result.errors.emails as unknown as Record<string, { message: string }>;
    expect(emails[1]).toMatchObject({ message: "Invalid email address" });
  });
});
