import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProfileScopeSubscriptionCard from "../ProfileScopeSubscriptionCard";
import { resolveActiveSubscription } from "@/lib/profile-subscription";

const { getPlanCatalog, listSubscriptions, useTenant } = vi.hoisted(() => ({
  getPlanCatalog: vi.fn(),
  listSubscriptions: vi.fn(),
  useTenant: vi.fn(),
}));

vi.mock("@/contexts/TenantContext", () => ({ useTenant }));
vi.mock("@/services/plan-settings-api", () => ({
  getPlanCatalog,
  listSubscriptions,
}));

const subscriptions = [
  {
    id: "org-sub",
    organization_id: "org-1",
    project_id: null,
    plan_code: "business",
    billing_period: "annual",
    status: "active",
    billing_status: "paid",
    current_period_start: "2026-01-01T00:00:00Z",
    current_period_end: "2027-01-01T00:00:00Z",
    trial: false,
    pilot: false,
    auto_renew: true,
    active_add_ons: [],
  },
  {
    id: "project-1-sub",
    organization_id: "org-1",
    project_id: "project-1",
    plan_code: "enterprise",
    billing_period: "annual",
    status: "active",
    billing_status: "paid",
    starts_at: "2026-02-01T00:00:00Z",
    ends_at: "2027-02-01T00:00:00Z",
    trial: false,
    pilot: false,
    auto_renew: false,
    active_add_ons: [],
  },
];

describe("ProfileScopeSubscriptionCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPlanCatalog.mockResolvedValue({
      plans: [
        { code: "business", name: "Business", family: "professional", tier: 2 },
        { code: "enterprise", name: "Enterprise", family: "enterprise", tier: 3 },
      ],
      add_ons: [],
      billing_periods: ["annual"],
      currency: "INR",
    });
    listSubscriptions.mockResolvedValue(subscriptions);
  });

  it("prefers the selected project's active subscription over the organisation plan", () => {
    expect(
      resolveActiveSubscription(subscriptions, "org-1", "project-1")?.id,
    ).toBe("project-1-sub");
    expect(
      resolveActiveSubscription(subscriptions, "org-1", "project-2")?.id,
    ).toBe("org-sub");
    expect(resolveActiveSubscription(subscriptions, "org-2", "project-1")).toBeNull();
  });

  it("shows authorised scope details and refreshes when the project changes", async () => {
    let project = { _id: "project-1", name: "North Corridor" };
    useTenant.mockImplementation(() => ({
      selectedOrganization: { _id: "org-1", name: "Acme Infrastructure" },
      selectedProject: project,
      selectedOrganizationId: "org-1",
      selectedProjectId: project._id,
      loading: false,
      error: null,
    }));

    const { rerender } = render(<ProfileScopeSubscriptionCard />);

    expect((await screen.findAllByText("Enterprise")).length).toBeGreaterThan(0);
    expect(screen.getByText("Project · North Corridor")).toBeInTheDocument();
    expect(screen.getByText("Enterprise · Tier 3")).toBeInTheDocument();

    project = { _id: "project-2", name: "South Corridor" };
    rerender(<ProfileScopeSubscriptionCard />);

    await waitFor(() => {
      expect(screen.getAllByText("Business").length).toBeGreaterThan(0);
      expect(
        screen.getByText("Organisation · Acme Infrastructure"),
      ).toBeInTheDocument();
      expect(listSubscriptions).toHaveBeenCalledTimes(2);
    });
  });
});
