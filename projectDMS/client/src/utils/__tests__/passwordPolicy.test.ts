import { describe, expect, it } from "vitest";
import { passwordPolicyError } from "../passwordPolicy";

// Mirrors backend utils/validation.py::validate_password_strength; the cases
// are the ones tests/test_password_reset_credential_contract.py refuses.
describe("passwordPolicyError", () => {
  it("accepts a password the server accepts", () => {
    expect(passwordPolicyError("New-Passw0rd-B")).toBeNull();
  });

  it.each([
    ["too short", "Ab1sh"],
    ["no uppercase", "alllowercase1"],
    ["no lowercase", "ALLUPPERCASE1"],
    ["no digit", "NoDigitsHere"],
    ["leading space", " New-Passw0rd-B"],
    ["trailing space", "New-Passw0rd-B "],
    ["trailing tab", "New-Passw0rd-B\t"],
  ])("refuses a password that is %s", (_label, password) => {
    expect(passwordPolicyError(password)).not.toBeNull();
  });
});
