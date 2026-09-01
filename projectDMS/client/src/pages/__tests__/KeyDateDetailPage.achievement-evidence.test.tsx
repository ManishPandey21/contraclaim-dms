import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getMilestone: vi.fn(),
  listEOTs: vi.fn(),
  getExtensionHistory: vi.fn(),
  getKeyDateWorkflow: vi.fn(),
}));

vi.mock("@/services/key-dates-api", () => ({
  ...api,
  recordAchievement: vi.fn(),
  reviewEOT: vi.fn(),
  submitEOT: vi.fn(),
}));
vi.mock("@/hooks/useHasPermission", () => ({ default: () => true }));
vi.mock("@/components/document-links/EntityDocumentLinks", () => ({
  default: (props: any) => (
    <div
      data-testid="achievement-evidence"
      data-target-type={props.targetType}
      data-target-id={props.targetId}
      data-default-role={props.defaultRole}
    />
  ),
}));

import KeyDateDetailPage from "../KeyDateDetailPage";

describe("KeyDateDetailPage achievement evidence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getMilestone.mockResolvedValue({
      id: "kd-1",
      _id: "kd-1",
      milestone_ref: "KD-01",
      title: "Complete foundations",
      contractual_week_number: 4,
      organization_id: "org-1",
      project_id: "project-1",
      contract_id: "primary",
      actual_achievement_date: "2026-08-03T00:00:00Z",
      client_notification_required: true,
      client_notification_ref: "CON/KD-01/ACH",
      client_notification_date: "2026-08-04T00:00:00Z",
      linked_document_ids: ["legacy-must-not-render"],
      linked_letter_ids: [],
      revisions: [],
      current_revision: 0,
    });
    api.listEOTs.mockResolvedValue([]);
    api.getExtensionHistory.mockResolvedValue([]);
    api.getKeyDateWorkflow.mockResolvedValue({
      project_id: "project-1",
      baseline_status: "frozen",
      submissions: [],
      determinations: [],
    });
  });

  it("keeps notification metadata distinct from event-level documentary evidence", async () => {
    render(
      <MemoryRouter initialEntries={["/key-dates/kd-1"]}>
        <Routes><Route path="/key-dates/:id" element={<KeyDateDetailPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("CON/KD-01/ACH")).toBeInTheDocument();
    const evidence = screen.getByTestId("achievement-evidence");
    expect(evidence).toHaveAttribute("data-target-type", "key_date_achievement");
    expect(evidence).toHaveAttribute("data-target-id", "kd-1:ach");
    expect(evidence).toHaveAttribute("data-default-role", "contractor_notification");
    expect(screen.queryByText("legacy-must-not-render")).not.toBeInTheDocument();
  });
});

