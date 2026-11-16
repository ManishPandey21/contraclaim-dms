import React from "react";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import WorkflowGateStepper from "@/components/arbitration/WorkflowGateStepper";
import { WORKFLOW_GATES, gateSteps } from "@/lib/workflow-gates";
import { containsRawId } from "@/lib/plain-language";

const at = (current_node: string, receipts: Record<string, string> = {}, extra: Record<string, unknown> = {}) => ({
  status: "awaiting_approval",
  current_node,
  required_human_role: null,
  approval_receipt_ids: receipts,
  ...extra,
});

describe("approval-gate stepper (4B-40)", () => {
  it("names the current gate and who acts there, with the earlier gates done", () => {
    render(
      <WorkflowGateStepper
        workflow={at("plan_approval_gate", { document_selection: "r1", matrix_review: "r2", readiness: "r3" })}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Now: Approve the pleading plan. Who acts: a senior legal approver.");
    const items = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(items).toHaveLength(8);
    expect(items[4]).toHaveAttribute("aria-current", "step");
    expect(items.slice(0, 4).every((item) => item.textContent?.includes("Done"))).toBe(true);
    expect(items.slice(5).every((item) => item.textContent?.includes("Not yet"))).toBe(true);
  });

  it("uses the role the server says is required", () => {
    render(<WorkflowGateStepper workflow={at("matrix_review_gate", { document_selection: "r1" }, { required_human_role: "quantum_reviewer" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Who acts: a quantum reviewer.");
  });

  it("says the system is working between gates, and when the run has stopped or finished", () => {
    const { rerender } = render(<WorkflowGateStepper workflow={at("generate_draft", { document_selection: "r1", matrix_review: "r2", readiness: "r3", plan: "r4" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Next: Legal review of the draft. The system is preparing it.");
    rerender(<WorkflowGateStepper workflow={at("legal_review_gate", { document_selection: "r1" }, { status: "cancelled" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Stopped at: Legal review of the draft.");
    rerender(<WorkflowGateStepper workflow={at("complete", {}, { status: "completed" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("All eight gates are done.");
  });

  it("covers the server's eight gates, and never shows an id or a node name", () => {
    expect(WORKFLOW_GATES.map((gate) => gate.gate)).toEqual([
      "document_selection", "material_question", "matrix_review", "readiness", "plan", "legal_review", "draft", "export",
    ]);
    for (const gate of WORKFLOW_GATES) {
      const { container, unmount } = render(<WorkflowGateStepper workflow={at(gate.node, { document_selection: "65f0c0ffee0000000000abcd" })} />);
      expect(containsRawId(container.textContent ?? "")).toBe(false);
      expect(container.textContent).not.toMatch(/_gate|_approval|[a-z]+_[a-z]+/);
      unmount();
    }
    expect(gateSteps(at("export_authorization_gate")).filter((step) => step.state === "current")).toHaveLength(1);
  });
});
