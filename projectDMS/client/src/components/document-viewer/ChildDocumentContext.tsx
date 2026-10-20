import React from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Paperclip } from "lucide-react";
import { Button } from "@/components/ui/button";
import { isChildDocument, relationshipLabel } from "@/utils/childDocument";

interface ChildDocumentContextProps {
  document: {
    parent_document_id?: string | null;
    relationship_type?: string | null;
    parent_provenance?: Record<string, any> | null;
  } | null;
}

/**
 * Names the parent a child document belongs to. The parent's letter number is
 * shown as the parent's, never as the child's own identity.
 */
const ChildDocumentContext: React.FC<ChildDocumentContextProps> = ({ document }) => {
  if (!document || !isChildDocument(document)) return null;
  const parentLetterNo = document.parent_provenance?.parent_letter_no;
  return (
    <section
      aria-label="Parent document"
      className="flex flex-wrap items-center justify-between gap-2 border-b border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-900"
    >
      <div className="flex min-w-0 items-center gap-2">
        <Paperclip className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="min-w-0 truncate" title={parentLetterNo || undefined}>
          {relationshipLabel(document.relationship_type)} to:{" "}
          <span className="font-medium">{parentLetterNo || "parent document"}</span>
        </span>
      </div>
      <Button asChild size="sm" variant="outline">
        <Link to={`/documentviewer/${document.parent_document_id}?tab=enclosure`}>
          <ArrowLeft className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
          Back to parent
        </Link>
      </Button>
    </section>
  );
};

export default ChildDocumentContext;
