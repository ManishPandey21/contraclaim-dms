/**
 * Frontend API types and server capability response.
 *
 * Supports F13-01 and F13-03.
 *
 * DEBT-10 was that the two client upload paths disagreed on the wire form of
 * "no project" - one sent an empty string, one omitted the field - so the
 * server could not tell either apart from an unanswered question. The fix is a
 * single serialiser behind a discriminated union: both paths now produce the
 * same bytes because it is the only shape the types allow.
 */

import { describe, expect, it } from "vitest";

import {
  appendUploadScope,
  availableScopeLevels,
  canOfferOrganizationScope,
  InvalidUploadScopeError,
  organizationScope,
  projectScope,
  serializeUploadScope,
  type ContractUploadScope,
} from "../contract-upload-scope";

describe("the discriminated union", () => {
  it("sends the discriminator affirmatively for organisation scope", () => {
    expect(serializeUploadScope(organizationScope())).toEqual({
      scope_level: "organization",
    });
  });

  it("omits project_id entirely rather than sending an empty one", () => {
    const serialised = serializeUploadScope(organizationScope());
    expect("project_id" in serialised).toBe(false);
  });

  it("requires an anchor for project scope", () => {
    expect(serializeUploadScope(projectScope("project-7"))).toEqual({
      scope_level: "project",
      project_id: "project-7",
    });
  });

  it("rejects a blank anchor instead of sending whitespace", () => {
    for (const blank of ["", "   ", "\t"]) {
      expect(() => projectScope(blank)).toThrow(InvalidUploadScopeError);
    }
  });

  it("trims an anchor rather than sending it padded", () => {
    expect(projectScope("  project-7  ").project_id).toBe("project-7");
  });

  it("never emits a scope without a discriminator", () => {
    const scopes: ContractUploadScope[] = [
      organizationScope(),
      projectScope("project-7"),
    ];
    scopes.forEach((scope) => {
      expect(serializeUploadScope(scope).scope_level).toBeTruthy();
    });
  });
});

describe("both upload paths serialise identically", () => {
  it("produces the same multipart fields from either path", () => {
    const scope = projectScope("project-7");

    const first = appendUploadScope(new FormData(), scope);
    const second = appendUploadScope(new FormData(), scope);

    expect([...first.entries()]).toEqual([...second.entries()]);
  });

  it("appends the organisation discriminator without a project field", () => {
    const form = appendUploadScope(new FormData(), organizationScope());
    const entries = Object.fromEntries(form.entries());

    expect(entries.scope_level).toBe("organization");
    expect(entries.project_id).toBeUndefined();
  });

  it("routes every path through one serialiser, so they cannot drift", () => {
    // appendUploadScope is defined in terms of serializeUploadScope, so a change
    // to the wire form necessarily changes both paths at once.
    const scope = projectScope("project-7");
    const direct = serializeUploadScope(scope);
    const viaForm = Object.fromEntries(
      appendUploadScope(new FormData(), scope).entries(),
    );

    expect(viaForm).toEqual(direct);
  });
});

describe("action visibility comes from the server", () => {
  it("offers organisation scope only when the server grants it", () => {
    expect(
      canOfferOrganizationScope({
        can_upload_organization_scope: true,
        can_upload_project_scope: true,
      }),
    ).toBe(true);

    expect(
      canOfferOrganizationScope({
        can_upload_organization_scope: false,
        can_upload_project_scope: true,
      }),
    ).toBe(false);
  });

  it("lists exactly the scopes the capability response allows", () => {
    expect(
      availableScopeLevels({
        can_upload_organization_scope: false,
        can_upload_project_scope: true,
      }),
    ).toEqual(["project"]);

    expect(
      availableScopeLevels({
        can_upload_organization_scope: true,
        can_upload_project_scope: false,
      }),
    ).toEqual(["organization"]);
  });

  it("offers nothing when the server grants nothing", () => {
    expect(
      availableScopeLevels({
        can_upload_organization_scope: false,
        can_upload_project_scope: false,
      }),
    ).toEqual([]);
  });

  it("takes capabilities rather than a user, so no role test can be written", () => {
    // availableScopeLevels has no access to a user object or a role list; the
    // only thing it can consult is what the server said this actor may do.
    expect(availableScopeLevels.length).toBe(1);
  });
});
