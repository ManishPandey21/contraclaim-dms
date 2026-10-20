// Phase 8 child documents (enclosures, annexures, attachments) in the UI.
//
// A child is evidence belonging to its parent letter, never a letter itself:
// the backend refuses to give it a letter number
// (`child_document_service.CHILD_LETTER_IDENTITY_FIELDS`), so the UI must not
// present one either, nor an incoming/outgoing direction.

type MaybeChild = { parent_document_id?: string | null } | null | undefined;

export const isChildDocument = (document: MaybeChild): boolean =>
  Boolean(document?.parent_document_id);

/** "enclosure" -> "Enclosure"; unknown or missing types read as "Enclosure". */
export const relationshipLabel = (type: string | null | undefined): string => {
  const value = (type || "enclosure").trim();
  return value.charAt(0).toUpperCase() + value.slice(1).toLowerCase();
};

/** Metadata fields that carry correspondence identity, which a child has none of. */
const CHILD_HIDDEN_FIELDS: ReadonlySet<string> = new Set(["uploadType", "letterNo"]);

export const withoutChildLetterIdentity = <T extends { id: string }>(
  fields: T[],
  document: MaybeChild
): T[] =>
  isChildDocument(document)
    ? fields.filter((field) => !CHILD_HIDDEN_FIELDS.has(field.id))
    : fields;

/**
 * Text for a child's processing status, so status never rides on colour alone.
 * Statuses are the ones `utils/processingStatus.ts` documents.
 */
export const childStatusText = (status: string | null | undefined): string => {
  switch ((status || "").toLowerCase()) {
    case "queued":
    case "retrying":
    case "not_queued":
      return "Queued";
    case "processing":
    case "metadata_extracted":
      return "Processing";
    case "completed":
      return "Completed";
    case "human_review_required":
      return "Review required";
    case "failed":
    case "dead_lettered":
      return "Failed";
    case "stored_only":
      return "Stored only";
    case "partially_processed":
      return "Partially processed";
    case "skipped":
      return "Skipped";
    default:
      return "Status unknown";
  }
};
