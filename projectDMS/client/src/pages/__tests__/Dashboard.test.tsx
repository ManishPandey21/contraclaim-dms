import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Dashboard from "../Dashboard";

const getDashboardStatsMock = vi.fn();

vi.mock("@/services/dashboard-api", () => ({
  getDashboardStats: (...args: any[]) => getDashboardStatsMock(...args),
}));

vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
  BarChart: ({ children }: any) => <div>{children}</div>,
  Bar: ({ children }: any) => <div>{children}</div>,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Cell: () => null,
  Legend: () => null,
  PieChart: ({ children }: any) => <div>{children}</div>,
  Pie: ({ children }: any) => <div>{children}</div>,
}));

describe("Dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders document-focused overview stats and the 6 newest uploaded documents", async () => {
    getDashboardStatsMock.mockResolvedValue({
      totalLetters: 7,
      totalDocuments: 7,
      inputRequiredCount: 1,
      incomingCount: 4,
      replyOverdueCount: 1,
      outgoingCount: 3,
      underReviewCount: 1,
      draftInProcessCount: 2,
      letterStatuses: [],
      documentStatuses: [
        { status: "Draft", count: 2, color: "#9CA3AF" },
        { status: "Review", count: 1, color: "#F97316" },
        { status: "Received", count: 2, color: "#3B82F6" },
        { status: "Completed", count: 2, color: "#22C55E" },
      ],
      recentActivity: [],
      recentDocuments: [
        {
          id: "doc-1",
          documentNumber: "DOC-001",
          filename: "doc-1.pdf",
          subject: "Oldest upload",
          direction: "Incoming",
          letterDate: "2026-03-01",
          uploadedAt: "2026-03-01T09:00:00Z",
        },
        {
          id: "doc-4",
          documentNumber: "DOC-004",
          filename: "doc-4.pdf",
          subject: "Fourth upload",
          direction: "Outgoing",
          letterDate: "2026-03-04",
          uploadedAt: "2026-03-04T09:00:00Z",
        },
        {
          id: "doc-7",
          documentNumber: "DOC-007",
          filename: "doc-7.pdf",
          subject: "Newest upload",
          direction: "Incoming",
          letterDate: "2026-03-07",
          uploadedAt: "2026-03-07T09:00:00Z",
        },
        {
          id: "doc-3",
          documentNumber: "DOC-003",
          filename: "doc-3.pdf",
          subject: "Third upload",
          direction: "Incoming",
          letterDate: "2026-03-03",
          uploadedAt: "2026-03-03T09:00:00Z",
        },
        {
          id: "doc-6",
          documentNumber: "DOC-006",
          filename: "doc-6.pdf",
          subject: "Sixth upload",
          direction: "Outgoing",
          letterDate: "2026-03-06",
          uploadedAt: "2026-03-06T09:00:00Z",
        },
        {
          id: "doc-2",
          documentNumber: "DOC-002",
          filename: "doc-2.pdf",
          subject: "Second upload",
          direction: "Outgoing",
          letterDate: "2026-03-02",
          uploadedAt: "2026-03-02T09:00:00Z",
        },
        {
          id: "doc-5",
          documentNumber: "DOC-005",
          filename: "doc-5.pdf",
          subject: "Fifth upload",
          direction: "Incoming",
          letterDate: "2026-03-05",
          uploadedAt: "2026-03-05T09:00:00Z",
        },
      ],
      actionableLetters: [],
      organizations: [],
      projects: [],
    });

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    expect(await screen.findByText("Total Documents")).toBeInTheDocument();
    expect(screen.getByText("Incoming")).toBeInTheDocument();
    expect(screen.getByText("Outgoing")).toBeInTheDocument();
    expect(screen.getByText("Draft in Process")).toBeInTheDocument();

    expect(screen.getByText("Document Status Breakdown")).toBeInTheDocument();
    expect(
      screen.getByText("Distribution of documents by current status")
    ).toBeInTheDocument();
    expect(screen.getByText("Newest uploaded documents")).toBeInTheDocument();
    expect(screen.queryByText("Letter Status Breakdown")).not.toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("DOC-007")).toBeInTheDocument();
    });

    const recentDocumentLabels = screen.getAllByText(/^DOC-00[2-7]$/);
    expect(recentDocumentLabels).toHaveLength(6);
    expect(recentDocumentLabels[0]).toHaveTextContent("DOC-007");
    expect(recentDocumentLabels[5]).toHaveTextContent("DOC-002");
    expect(screen.queryByText("DOC-001")).not.toBeInTheDocument();
  });
});
