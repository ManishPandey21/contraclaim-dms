import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ProjectsPage from "../ProjectsPage";

const getProjectsMock = vi.fn();
const getOrganizationsMock = vi.fn();
const getProjectStatsMock = vi.fn();

vi.mock("@/services/enhanced-api", () => {
  return {
    __esModule: true,
    enhancedApi: {
      getProjects: (...args: any[]) => getProjectsMock(...args),
      getOrganizations: (...args: any[]) => getOrganizationsMock(...args),
      getProjectStats: (...args: any[]) => getProjectStatsMock(...args),
      deactivateProject: vi.fn(),
    },
  };
});

vi.mock("@/hooks/useRBAC", () => {
  return {
    __esModule: true,
    default: () => ({ roles: ["superadmin"] }),
  };
});

vi.mock("@/hooks/useHasPermission", () => {
  return {
    __esModule: true,
    default: () => false,
  };
});

vi.mock("@/services/auth", () => {
  return {
    __esModule: true,
    redirectToLoginAfterSessionExpiry: vi.fn(),
  };
});

vi.mock("@/services/session-api", () => {
  return {
    __esModule: true,
    getCurrentUserProfile: vi.fn().mockResolvedValue({
      id: "superadmin-id",
      email: "superadmin@example.com",
      roles: ["superadmin"],
      organization_id: null,
      projects: [],
    }),
  };
});

/** Return the card element that contains the given project heading. */
const cardFor = (projectName: string) => {
  const heading = screen.getByText(projectName);
  const card = heading.closest("div.overflow-hidden");
  if (!card) throw new Error(`No card found for ${projectName}`);
  return card as HTMLElement;
};

/** Read the value rendered above a "Letters" / "Team Members" caption. */
const statValue = (card: HTMLElement, caption: string) => {
  const label = within(card).getByText(caption);
  const block = label.parentElement as HTMLElement;
  const value = block.querySelector("span.font-bold");
  return value?.textContent ?? null;
};

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={["/projects"]}>
      <Routes>
        <Route path="/projects" element={<ProjectsPage />} />
      </Routes>
    </MemoryRouter>
  );

describe("ProjectsPage card counts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getProjectsMock.mockResolvedValue([
      {
        _id: "project-1",
        name: "Alpha Project",
        organization_id: "org-1",
        status: "Active",
      },
      {
        _id: "project-2",
        name: "Beta Project",
        organization_id: "org-1",
        status: "Active",
      },
    ]);
    getOrganizationsMock.mockResolvedValue([{ _id: "org-1", name: "Org One" }]);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders the live per-project counts returned by the aggregated endpoint", async () => {
    getProjectStatsMock.mockResolvedValue([
      {
        project_id: "project-1",
        letterCount: 12,
        incomingCount: 7,
        outgoingCount: 5,
        teamSize: 4,
      },
      {
        project_id: "project-2",
        letterCount: 0,
        incomingCount: 0,
        outgoingCount: 0,
        teamSize: 0,
      },
    ]);

    renderPage();
    expect(await screen.findByText("Alpha Project")).toBeInTheDocument();
    await screen.findByText("12");

    const alpha = cardFor("Alpha Project");
    expect(statValue(alpha, "Letters")).toBe("12");
    expect(statValue(alpha, "Team Members")).toBe("4");

    // A project with no letters and no members legitimately shows zero.
    const beta = cardFor("Beta Project");
    expect(statValue(beta, "Letters")).toBe("0");
    expect(statValue(beta, "Team Members")).toBe("0");

    // One aggregated request for the whole page, not one per card.
    expect(getProjectStatsMock).toHaveBeenCalledTimes(1);
  });

  it("shows a loading placeholder until the counts arrive", async () => {
    let releaseStats: (value: unknown[]) => void = () => undefined;
    getProjectStatsMock.mockReturnValue(
      new Promise((resolve) => {
        releaseStats = resolve as (value: unknown[]) => void;
      })
    );

    renderPage();
    expect(await screen.findByText("Alpha Project")).toBeInTheDocument();

    expect(screen.getAllByLabelText("Loading letter count").length).toBe(2);
    expect(screen.getAllByLabelText("Loading team member count").length).toBe(2);

    releaseStats([
      {
        project_id: "project-1",
        letterCount: 3,
        incomingCount: 2,
        outgoingCount: 1,
        teamSize: 2,
      },
      {
        project_id: "project-2",
        letterCount: 1,
        incomingCount: 1,
        outgoingCount: 0,
        teamSize: 1,
      },
    ]);

    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.queryByLabelText("Loading letter count")).toBeNull();
  });

  it("falls back to an unavailable marker instead of zero when the counts fail", async () => {
    getProjectStatsMock.mockRejectedValue(new Error("boom"));

    renderPage();
    expect(await screen.findByText("Alpha Project")).toBeInTheDocument();
    await screen.findAllByLabelText("letter count unavailable");

    const alpha = cardFor("Alpha Project");
    expect(statValue(alpha, "Letters")).not.toBe("0");
    expect(statValue(alpha, "Team Members")).not.toBe("0");
    expect(
      screen.getAllByLabelText("team member count unavailable").length
    ).toBe(2);
  });

  it("does not invent a zero for a project missing from the stats response", async () => {
    getProjectStatsMock.mockResolvedValue([
      {
        project_id: "project-1",
        letterCount: 5,
        incomingCount: 3,
        outgoingCount: 2,
        teamSize: 1,
      },
    ]);

    renderPage();
    expect(await screen.findByText("Alpha Project")).toBeInTheDocument();
    await screen.findByText("5");

    const beta = cardFor("Beta Project");
    expect(statValue(beta, "Letters")).not.toBe("0");
    expect(
      within(beta).getAllByLabelText(/unavailable/).length
    ).toBe(2);
  });
});
