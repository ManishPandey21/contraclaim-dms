import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import ChildDocumentContext from "@/components/document-viewer/ChildDocumentContext";
import { isChildDocument, withoutChildLetterIdentity } from "@/utils/childDocument";

const PARENT_NO = "GLM-SAM-KNPCC-05-UPMRC-OL-2026-4951";

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function renderBanner(document: Record<string, unknown>) {
  return render(
    <MemoryRouter initialEntries={["/documentviewer/child-1"]}>
      <Routes>
        <Route
          path="/documentviewer/child-1"
          element={<ChildDocumentContext document={document} />}
        />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>
  );
}

const CHILD = {
  _id: "child-1",
  parent_document_id: "parent-1",
  relationship_type: "enclosure",
  parent_provenance: { parent_letter_no: PARENT_NO, parent_upload_type: "incoming" },
};

describe("ChildDocumentContext", () => {
  it("shows the parent provenance and the relationship type", () => {
    renderBanner(CHILD);
    const banner = screen.getByRole("region", { name: "Parent document" });
    expect(banner).toHaveTextContent(`Enclosure to: ${PARENT_NO}`);
  });

  it("navigates Back to parent onto the parent's Enclosures tab", async () => {
    renderBanner(CHILD);
    fireEvent.click(screen.getByRole("link", { name: "Back to parent" }));
    expect(await screen.findByTestId("location")).toHaveTextContent(
      "/documentviewer/parent-1?tab=enclosure"
    );
  });

  it("names an annexure as such and does not invent a parent number", () => {
    renderBanner({ ...CHILD, relationship_type: "annexure", parent_provenance: null });
    const banner = screen.getByRole("region", { name: "Parent document" });
    expect(banner).toHaveTextContent("Annexure to: parent document");
  });

  it("renders nothing for a top-level document", () => {
    const { container } = renderBanner({ _id: "doc-1", parent_document_id: null });
    expect(container).toBeEmptyDOMElement();
  });
});

describe("child-not-letter metadata", () => {
  const fields = [
    { id: "uploadType", label: "Direction" },
    { id: "date", label: "Date" },
    { id: "letterNo", label: "Letter No." },
    { id: "subject", label: "Subject" },
  ];

  it("drops Letter No. and Direction for a child", () => {
    expect(isChildDocument(CHILD)).toBe(true);
    expect(withoutChildLetterIdentity(fields, CHILD).map((f) => f.id)).toEqual([
      "date",
      "subject",
    ]);
  });

  it("keeps every field for a top-level letter", () => {
    const letter = { _id: "doc-1", parent_document_id: null };
    expect(isChildDocument(letter)).toBe(false);
    expect(withoutChildLetterIdentity(fields, letter)).toEqual(fields);
  });
});
