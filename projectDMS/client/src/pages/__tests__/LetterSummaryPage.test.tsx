import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import LetterSummaryPage from "../LetterSummaryPage";

const { mockUseHasPermission, mockToast } = vi.hoisted(() => ({
  mockUseHasPermission: vi.fn(() => true),
  mockToast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/hooks/useHasPermission", () => ({
  default: mockUseHasPermission,
}));

vi.mock("sonner", () => ({
  toast: mockToast,
}));

Object.assign(navigator, {
  clipboard: {
    writeText: vi.fn().mockResolvedValue(undefined),
  },
});

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = String(value);
    },
    clear: () => {
      store = {};
    },
    removeItem: (key: string) => {
      delete store[key];
    },
  };
})();

Object.defineProperty(window, "localStorage", {
  value: localStorageMock,
});

let currentDoc: any = null;
let lastPatchPayload: any = null;

const makeDocument = () => ({
  id: "doc-1",
  letterNo: "AFC/PM/KNPCC-06/4930",
  date: "2025-11-17",
  subject:
    "Reminder for Submission of Construction Cost for Additional Borewell",
  from_: "AFCONS- SAM INDIA CONSORTIUM",
  to: "Typsa - Italferr JV",
  summary:
    "- The letter addresses construction of an additional borewell as a variation.",
  contractual_clauses: ["sub-clause 12.3 and 17.1 of General Conditions Contract"],
  key_reply_points: ["Confirm entitlement and request supporting records."],
  keywords: ["borewell", "Kanpur", "variation"],
  additional_keywords: ["payment", "contract"],
  extracted_tags: ["Tunnel", "Variation", "Payment"],
  extracted_subTags: ["Kanpur", "Borewell", "Additional Work"],
  asset_type: "Tunnel",
  location: "Kanpur",
  work_type: "Borewell construction",
  issue_nature: "Variation",
  claim_category: "Additional Work Claim",
  alleged_responsibility: "Employer",
  metadata: {
    tags: ["Tunnel", "Variation", "Payment"],
    subTags: ["Kanpur", "Borewell", "Additional Work"],
  },
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

beforeEach(() => {
  currentDoc = makeDocument();
  lastPatchPayload = null;
  mockUseHasPermission.mockReturnValue(true);
  mockToast.success.mockClear();
  mockToast.error.mockClear();
  (window.localStorage as any).clear();
  vi.mocked(global.fetch).mockClear();
});

global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = String(init?.method || "GET").toUpperCase();

  if (url.match(/\/api\/documents\/([^/]+)\/summary-metadata$/) && method === "PATCH") {
    lastPatchPayload = JSON.parse(String(init?.body || "{}"));
    const metadata = lastPatchPayload.extracted_metadata;
    if (metadata) {
      currentDoc = {
        ...currentDoc,
        asset_type: metadata.asset_type,
        location: metadata.location,
        work_type: metadata.work_type,
        issue_nature: metadata.issue_nature,
        claim_category: metadata.claim_category,
        alleged_responsibility: metadata.responsibility,
        metadata: {
          ...currentDoc.metadata,
          asset_type: metadata.asset_type,
          location: metadata.location,
          work_type: metadata.work_type,
          issue_nature: metadata.issue_nature,
          claim_category: metadata.claim_category,
          responsibility: metadata.responsibility,
        },
      };
    }
    if ("keywords" in lastPatchPayload) {
      currentDoc = { ...currentDoc, keywords: lastPatchPayload.keywords };
    }
    if ("additional_keywords" in lastPatchPayload) {
      currentDoc = {
        ...currentDoc,
        additional_keywords: lastPatchPayload.additional_keywords,
      };
    }
    if ("extracted_tags" in lastPatchPayload) {
      currentDoc = {
        ...currentDoc,
        extracted_tags: lastPatchPayload.extracted_tags,
      };
    }
    if ("extracted_sub_tags" in lastPatchPayload) {
      currentDoc = {
        ...currentDoc,
        extracted_subTags: lastPatchPayload.extracted_sub_tags,
      };
    }
    return jsonResponse(currentDoc);
  }

  if (url.match(/\/api\/documents\/([^/]+)\/comments$/)) {
    return jsonResponse([]);
  }

  if (url.match(/\/api\/documents\/([^/]+)$/)) {
    return currentDoc ? jsonResponse(currentDoc) : jsonResponse({}, 404);
  }

  return jsonResponse({}, 404);
}) as any;

function renderAt(letterId = "doc-1") {
  return render(
    <MemoryRouter initialEntries={[`/documents/summary/${letterId}`]}>
      <Routes>
        <Route path="/documents/summary/:id" element={<LetterSummaryPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("LetterSummaryPage summary metadata editing", () => {
  it("hides edit controls when the user lacks metadata edit permission", async () => {
    mockUseHasPermission.mockReturnValue(false);

    renderAt();

    await screen.findByText(currentDoc.subject);

    expect(screen.queryByRole("button", { name: /^edit$/i })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /edit keywords/i })
    ).not.toBeInTheDocument();
  });

  it("saves edited extracted metadata through the summary metadata endpoint", async () => {
    const user = userEvent.setup();
    renderAt();

    await screen.findByText(currentDoc.subject);
    await user.click(screen.getByRole("button", { name: /^edit$/i }));

    const dialog = screen.getByRole("dialog", {
      name: /edit extracted metadata/i,
    });
    await user.clear(within(dialog).getByLabelText(/asset type/i));
    await user.type(within(dialog).getByLabelText(/asset type/i), " Station Box ");
    await user.clear(within(dialog).getByLabelText(/location/i));
    await user.type(within(dialog).getByLabelText(/location/i), " Kanpur South ");
    await user.clear(within(dialog).getByLabelText(/responsibility/i));
    await user.type(within(dialog).getByLabelText(/responsibility/i), " Contractor ");
    await user.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(lastPatchPayload).toEqual({
        extracted_metadata: {
          asset_type: "Station Box",
          location: "Kanpur South",
          work_type: "Borewell construction",
          issue_nature: "Variation",
          claim_category: "Additional Work Claim",
          responsibility: "Contractor",
        },
      });
    });
    expect(await screen.findByText("Station Box")).toBeInTheDocument();
    expect(screen.getByText("Kanpur South")).toBeInTheDocument();
    expect(mockToast.success).toHaveBeenCalledWith("Letter summary metadata saved");
  });

  it("adds, edits, deletes, trims, and dedupes keyword chips before save", async () => {
    const user = userEvent.setup();
    renderAt();

    await screen.findByText(currentDoc.subject);
    await user.click(screen.getByRole("button", { name: /edit keywords/i }));

    const dialog = screen.getByRole("dialog", { name: /edit keywords/i });
    await user.click(
      within(dialog).getByRole("button", { name: /^Remove borewell$/ })
    );
    await user.clear(within(dialog).getByLabelText(/^Keywords item 1$/));
    await user.type(
      within(dialog).getByLabelText(/^Keywords item 1$/),
      " Kanpur Metro "
    );
    await user.type(
      within(dialog).getByPlaceholderText(/add keyword/i),
      " payment, Payment, delay "
    );
    await user.click(within(dialog).getAllByRole("button", { name: /^add$/i })[0]);

    await user.click(within(dialog).getByRole("button", { name: /remove contract/i }));
    await user.type(
      within(dialog).getByPlaceholderText(/add additional keyword/i),
      " payment, delay "
    );
    await user.click(within(dialog).getAllByRole("button", { name: /^add$/i })[1]);

    await user.clear(within(dialog).getByLabelText(/^Extracted Tags item 1$/));
    await user.type(
      within(dialog).getByLabelText(/^Extracted Tags item 1$/),
      " Tunnel Package "
    );

    await user.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(lastPatchPayload).toMatchObject({
        keywords: ["Kanpur Metro", "variation", "payment", "delay"],
        additional_keywords: ["payment", "delay"],
        extracted_tags: ["Tunnel Package", "Variation", "Payment"],
        extracted_sub_tags: ["Kanpur", "Borewell", "Additional Work"],
      });
    });
    expect(await screen.findByText("Kanpur Metro")).toBeInTheDocument();
    expect(screen.getByText("Tunnel Package")).toBeInTheDocument();
    expect(mockToast.success).toHaveBeenCalledWith("Letter summary metadata saved");
  });
});

describe("LetterSummaryPage extraction quality", () => {
  it("warns when the stored metadata is a partial extraction", async () => {
    currentDoc = {
      ...makeDocument(),
      metadata_quality: { status: "partial_extraction", degraded: true },
    };
    renderAt();

    await screen.findByText(currentDoc.subject);
    expect(
      screen.getByRole("status", { name: "" })
    ).toHaveTextContent(/only partially extracted/i);
  });

  it("shows no warning for a complete extraction", async () => {
    currentDoc = { ...makeDocument(), metadata_quality: { status: "complete" } };
    renderAt();

    await screen.findByText(currentDoc.subject);
    expect(screen.queryByText(/only partially extracted/i)).not.toBeInTheDocument();
  });

  it("labels key reply points as AI advice, not letter content", async () => {
    const user = userEvent.setup();
    renderAt();

    await screen.findByText(currentDoc.subject);
    await user.click(screen.getByRole("tab", { name: /key reply points/i }));
    expect(screen.getAllByText(/not statements made in this letter/i).length).toBeGreaterThan(0);
  });
});
