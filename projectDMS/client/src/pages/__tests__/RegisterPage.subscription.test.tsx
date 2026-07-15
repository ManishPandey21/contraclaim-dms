import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RegisterPage from "../RegisterPage";
import {
  NO_SERVICE_PLAN_CODE,
  buildOrganizationPlanScopePayload,
  buildOrganizationProfilePayload,
  organizationSubscriptionChanged,
  organizationSubscriptionSnapshotFromEffective,
} from "@/lib/organization-subscription";

const mocks = vi.hoisted(() => {
  const enhancedApiMock = {
    getOrganizations: vi.fn(),
    getOrganization: vi.fn(),
    updateOrganization: vi.fn(),
    createOrganization: vi.fn(),
    createUser: vi.fn(),
  };
  const planApiMock = {
    getPlanCatalog: vi.fn(),
    getPlanSettings: vi.fn(),
    updatePlanSettingsScope: vi.fn(),
  };
  return {
    enhancedApiMock,
    planApiMock,
    requestTokenMock: vi.fn(),
    toastMock: vi.fn(),
  };
});

vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: mocks.enhancedApiMock,
}));

vi.mock("@/services/plan-settings-api", () => ({
  getPlanCatalog: mocks.planApiMock.getPlanCatalog,
  getPlanSettings: mocks.planApiMock.getPlanSettings,
  updatePlanSettingsScope: mocks.planApiMock.updatePlanSettingsScope,
}));

vi.mock("@/hooks/use-toast", () => ({
  useToast: () => ({ toast: mocks.toastMock }),
}));

vi.mock("@/hooks/useStepUp", () => ({
  useStepUp: () => ({
    requestToken: mocks.requestTokenMock,
    StepUpDialog: null,
  }),
}));

vi.mock("@/hooks/useHasPermission", () => ({
  default: () => true,
}));

const validOrg = {
  _id: "org_1",
  name: "Dineshchandra R. Agrawal Infracon Pvt. Ltd",
  panNumber: "AABCD9523D",
  gstNumber: "24AABCD9523D1Z0",
  address: "401, The Grand Mall, S.M. Road",
  city: "Ahmedabad",
  state: "Gujarat",
  pinCode: "380015",
  adminName: "Rakesh Sinha",
  adminEmail: "info@draipl.com",
  adminContact: "07926309789",
  billingEnabled: true,
};

describe("RegisterPage organization subscription edit flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.enhancedApiMock.getOrganizations.mockResolvedValue([]);
    mocks.enhancedApiMock.getOrganization.mockResolvedValue(validOrg);
    mocks.enhancedApiMock.updateOrganization.mockResolvedValue(validOrg);
    mocks.planApiMock.getPlanCatalog.mockResolvedValue({
      plans: [
        {
          code: "dms_pro",
          name: "DMS Professional",
          is_active: true,
          pricing_tiers: { monthly: 7500000 },
        },
      ],
      add_ons: [],
      billing_periods: ["monthly", "quarterly", "annual"],
      currency: "INR",
    });
    mocks.planApiMock.getPlanSettings.mockResolvedValue({
      organizations: [{ id: "org_1", name: validOrg.name }],
      projects: [],
      plans: [],
      subscriptions: [],
      effective: {
        organizations: {
          org_1: {
            source: "organization",
            subscription_id: "sub_1",
            plan_code: "dms_pro",
            plan_name: "DMS Professional",
            billing_period: "annual",
            trial: false,
            dms_enabled: true,
            drafting_enabled: false,
          },
        },
        projects: {},
      },
    });
    mocks.planApiMock.updatePlanSettingsScope.mockResolvedValue({});
    mocks.requestTokenMock.mockResolvedValue("step-token");
  });

  it("loads current subscription in edit mode and does not send subscription fields to organization update", async () => {
    render(
      <MemoryRouter
        initialEntries={[
          "/register?tab=organization&mode=edit&orgId=org_1",
        ]}
      >
        <Routes>
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/organizations" element={<div>Organizations</div>} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() =>
      expect(mocks.enhancedApiMock.getOrganization).toHaveBeenCalledWith(
        "org_1"
      )
    );
    await waitFor(() =>
      expect(screen.getByDisplayValue(validOrg.name)).toBeInTheDocument()
    );

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() =>
      expect(mocks.enhancedApiMock.updateOrganization).toHaveBeenCalled()
    );
    const [, payload] = mocks.enhancedApiMock.updateOrganization.mock.calls[0];
    expect(payload).not.toHaveProperty("plan_code");
    expect(payload).not.toHaveProperty("billing_period");
    expect(payload).not.toHaveProperty("trial_enabled");
    expect(mocks.planApiMock.updatePlanSettingsScope).not.toHaveBeenCalled();
    expect(mocks.requestTokenMock).not.toHaveBeenCalled();
  });

  it("builds profile and subscription scope payloads separately", () => {
    const formData = {
      ...validOrg,
      plan_code: "dms_pro",
      billing_period: "annual",
      trial_enabled: false,
    };

    expect(buildOrganizationProfilePayload(formData as any)).not.toHaveProperty(
      "plan_code"
    );
    expect(buildOrganizationPlanScopePayload("org_1", formData as any)).toMatchObject({
      organization_id: "org_1",
      project_id: null,
      mode: "plan",
      plan_code: "dms_pro",
      billing_period: "annual",
      status: "active",
    });
    expect(
      buildOrganizationPlanScopePayload("org_1", {
        plan_code: NO_SERVICE_PLAN_CODE,
      })
    ).toMatchObject({
      mode: "no_service",
      plan_code: null,
    });
  });

  it("detects subscription changes from effective plan snapshots", () => {
    const initial = organizationSubscriptionSnapshotFromEffective({
      source: "organization",
      plan_code: "dms_pro",
      billing_period: "annual",
      dms_enabled: true,
      drafting_enabled: false,
    });

    expect(
      organizationSubscriptionChanged(initial, {
        plan_code: "dms_pro",
        billing_period: "annual",
      })
    ).toBe(false);
    expect(
      organizationSubscriptionChanged(initial, {
        billing_period: "annual",
      })
    ).toBe(false);
    expect(
      organizationSubscriptionChanged(initial, {
        plan_code: "dms_pro",
        billing_period: "monthly",
      })
    ).toBe(true);
  });
});
