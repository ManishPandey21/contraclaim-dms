import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("@/services/api", () => ({
  api: { get: mocks.get },
}));

import {
  getCurrentEntitlements,
  type CurrentEntitlements,
} from "../plan-settings-api";

const entitled: CurrentEntitlements = {
  contract_version: "test-contract",
  scope_mode: "organization",
  organization_id: "org-A",
  project_id: null,
  source: "organization",
  features: { "feature.dms.enabled": true },
  dms_enabled: true,
  drafting_enabled: false,
  unavailable_reason: null,
};

describe("getCurrentEntitlements", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    vi.mocked(window.localStorage.getItem).mockReset();
    vi.mocked(window.localStorage.getItem).mockReturnValue(null);
  });

  it("deduplicates concurrent readers for the same selected scope", async () => {
    let resolveRequest!: (value: { data: CurrentEntitlements }) => void;
    const pending = new Promise<{ data: CurrentEntitlements }>((resolve) => {
      resolveRequest = resolve;
    });
    mocks.get.mockReturnValueOnce(pending);
    vi.mocked(window.localStorage.getItem).mockImplementation((key) =>
      key === "org_id" ? "org-A" : null,
    );

    const routeGuardRequest = getCurrentEntitlements();
    const sidebarRequest = getCurrentEntitlements();

    expect(mocks.get).toHaveBeenCalledTimes(1);
    resolveRequest({ data: entitled });
    await expect(Promise.all([routeGuardRequest, sidebarRequest])).resolves.toEqual([
      entitled,
      entitled,
    ]);
  });

  it("does not share an in-flight response across different scopes", async () => {
    let resolveOrgA!: (value: { data: CurrentEntitlements }) => void;
    let resolveOrgB!: (value: { data: CurrentEntitlements }) => void;
    mocks.get
      .mockReturnValueOnce(
        new Promise<{ data: CurrentEntitlements }>((resolve) => {
          resolveOrgA = resolve;
        }),
      )
      .mockReturnValueOnce(
        new Promise<{ data: CurrentEntitlements }>((resolve) => {
          resolveOrgB = resolve;
        }),
      );
    let selectedOrganizationId = "org-A";
    vi.mocked(window.localStorage.getItem).mockImplementation((key) =>
      key === "org_id" ? selectedOrganizationId : null,
    );
    const first = getCurrentEntitlements();

    selectedOrganizationId = "org-B";
    const second = getCurrentEntitlements();

    expect(mocks.get).toHaveBeenCalledTimes(2);
    resolveOrgA({ data: entitled });
    resolveOrgB({
      data: { ...entitled, organization_id: "org-B" },
    });
    await Promise.all([first, second]);
  });
});
