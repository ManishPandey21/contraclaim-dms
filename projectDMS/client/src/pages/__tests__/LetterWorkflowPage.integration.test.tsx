import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import LetterWorkflowPage from "../LetterWorkflowPage";

// Mock useLetterWorkflow hook to control state and capture calls
const handleLetterInitiationMock = vi.fn().mockResolvedValue({
  id: "L1",
  status: "Draft",
  letter_no: "LET-123",
});
const handleLetterUpdateMock = vi.fn().mockResolvedValue({
  id: "L1",
  status: "Replied",
});

vi.mock("@/hooks/useLetterWorkflow", () => {
  return {
    useLetterWorkflow: () => ({
      activeTab: "All",
      setActiveTab: vi.fn(),
      isInitiateDialogOpen: true,
      setIsInitiateDialogOpen: vi.fn(),
      isRequestInputDialogOpen: false,
      setIsRequestInputDialogOpen: vi.fn(),
      selectedLetter: null,
      setSelectedLetter: vi.fn(),
      users: [{ id: "u1", name: "Alice", email: "a@example.com" }],
      organizations: [{ id: "o1", name: "Org" }],
      projects: [{ id: "p1", name: "Proj", organizationId: "o1" }],
      letters: [],
      isLoading: false,
      error: null,
      handleLetterInitiation: handleLetterInitiationMock,
      handleLetterUpdate: handleLetterUpdateMock,
      handleInputRequest: vi.fn().mockResolvedValue(undefined),
      formatDate: (s: string) => s,
      getFilteredLetters: () => [],
    }),
  };
});

// Mock enhancedApi calls used by the page
const requestDraftForDocumentMock = vi.fn().mockResolvedValue({});
const completeDraftForDocumentMock = vi.fn().mockResolvedValue({});
const getDocumentMock = vi.fn().mockResolvedValue({
  _id: "doc-1",
  organization_id: "o1",
  project_id: "p1",
  letterNo: "LET-XYZ",
  status: "Received",
});
const getLetterMock = vi.fn();

vi.mock("@/services/enhanced-api", () => {
  return {
    __esModule: true,
    default: {
      requestDraftForDocument: (...args: any[]) =>
        requestDraftForDocumentMock(...args),
      completeDraftForDocument: (...args: any[]) =>
        completeDraftForDocumentMock(...args),
      getDocument: (...args: any[]) => getDocumentMock(...args),
      getLetter: (...args: any[]) => getLetterMock(...args),
    },
    enhancedApi: {
      requestDraftForDocument: (...args: any[]) =>
        requestDraftForDocumentMock(...args),
      completeDraftForDocument: (...args: any[]) =>
        completeDraftForDocumentMock(...args),
      getDocument: (...args: any[]) => getDocumentMock(...args),
      getLetter: (...args: any[]) => getLetterMock(...args),
    },
  };
});

// Stub LetterInitiationForm to a one-click submitter
vi.mock("@/components/letter-workflow/LetterInitiationForm", () => {
  return {
    __esModule: true,
    default: (props: any) => {
      return (
        <button
          aria-label="stub-init-submit"
          onClick={() =>
            props.onSubmit({
              subject: "Subject",
              recipient: "Recipient",
              assigned_to: "u1",
              organization_id: "o1",
              project_id: "p1",
            })
          }
        >
          Submit Init
        </button>
      );
    },
  };
});

// Stub LetterDialog to a one-click completion
vi.mock("@/components/letter-workflow/LetterDialog", () => {
  return {
    __esModule: true,
    LetterDialog: (props: any) => {
      return (
        <button
          aria-label="stub-complete"
          onClick={() =>
            props.onLetterUpdate({
              id: "L1",
              title: "t",
              recipient: "r",
              subject: "s",
              content: "c",
              status: "Replied",
            })
          }
        >
          Complete Draft
        </button>
      );
    },
  };
});

// Keep other components as-is or shallow enough not to impede

describe("LetterWorkflowPage integration: request draft and complete", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("reserves draft for document, initiates letter, and completes setting document to Replied", async () => {
    render(
      <MemoryRouter
        initialEntries={["/letters?requestDraftForDocumentId=doc-1"]}
      >
        <Routes>
          <Route path="/letters" element={<LetterWorkflowPage />} />
        </Routes>
      </MemoryRouter>
    );

    // Wait for the initiating document to be fetched
    await waitFor(() => {
      expect(getDocumentMock).toHaveBeenCalledWith("doc-1");
    });

    // Click the stub to initiate the letter
    const submitInitBtn = await screen.findByLabelText("stub-init-submit");
    fireEvent.click(submitInitBtn);

    // First, the server-side guard should reserve the draft for the document
    await waitFor(() => {
      expect(requestDraftForDocumentMock).toHaveBeenCalledWith("doc-1");
    });

    // Then, initiation should be called
    await waitFor(() => {
      expect(handleLetterInitiationMock).toHaveBeenCalled();
    });

    // Now simulate completion via the stub
    const completeBtn = await screen.findByLabelText("stub-complete");
    fireEvent.click(completeBtn);

    // After completion, page should call completeDraftForDocument (set document to Replied)
    await waitFor(() => {
      expect(completeDraftForDocumentMock).toHaveBeenCalledWith("doc-1");
    });

    // And update handler should be called with status Replied
    expect(handleLetterUpdateMock).toHaveBeenCalled();
  });
});
