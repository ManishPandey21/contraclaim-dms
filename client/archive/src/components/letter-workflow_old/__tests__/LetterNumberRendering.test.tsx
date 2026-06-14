import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import LetterInputComponent from "../LetterInputComponent";
import LetterDraftEditor from "../LetterDraftEditor";

vi.mock("@/services/api", () => {
  return { api: { get: vi.fn(), post: vi.fn() } };
});

// For LetterDraftEditor, mock RequestInputForm to avoid network
vi.mock("../RequestInputForm", () => {
  return {
    __esModule: true,
    default: () => <div data-testid="request-input-form-stub" />,
  };
});

describe("Letter number rendering", () => {
  it("LetterInputComponent shows letter_no when present", () => {
    const letter: any = {
      id: "L1",
      title: "T",
      recipient: "R",
      subject: "S",
      content: "",
      status: "Draft",
      createdBy: { id: "u1" },
      assignedTo: { id: "u2" },
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      letter_no: "LET-123",
    };

    render(
      <LetterInputComponent
        letter={letter}
        onInput={() => {}}
        onCancel={() => {}}
      />
    );

    expect(screen.getByText("Letter Number")).toBeInTheDocument();
    expect(screen.getByText("LET-123")).toBeInTheDocument();
  });

  it("LetterDraftEditor shows letter_no when present", () => {
    const letter: any = {
      id: "L2",
      title: "T",
      recipient: "R",
      subject: "S",
      content: "",
      status: "Draft",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      letter_no: "LET-999",
      organization_id: "o1",
      project_id: "p1",
    };

    render(
      <LetterDraftEditor
        letter={letter}
        onSave={() => {}}
        onCancel={() => {}}
      />
    );

    expect(screen.getByText("Letter Number")).toBeInTheDocument();
    expect(screen.getByText("LET-999")).toBeInTheDocument();
  });
});
