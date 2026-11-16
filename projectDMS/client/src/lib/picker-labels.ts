import type { DocumentItem } from "@/services/documents-api";

export type DocumentLabel = (doc: DocumentItem) => string;

/** A document's name for display. Never its id (4B-11). */
export const documentName: DocumentLabel = (doc) =>
  (doc.name || doc.filename || doc.subject || "").trim() || "Untitled document";

/** Shown for a linked id that cannot be read: removed, or restricted for this user. */
export const UNAVAILABLE = "Restricted or removed document";

/** Correspondence, as the upload screens record it. */
export const LETTER_UPLOAD_TYPES = new Set(["incoming", "outgoing", "correspondence"]);

export const isLetter = (doc: DocumentItem) => LETTER_UPLOAD_TYPES.has(String(doc.uploadType || "").toLowerCase());

/** "ABC/123 · Delayed handover · 2026-04-11": reference, subject and date, never an id. */
export const letterLabel = (doc: DocumentItem): string => {
  const parts = [doc.letterNo, doc.subject || documentName(doc), doc.date ? String(doc.date).slice(0, 10) : null]
    .map((part) => (part ? String(part).trim() : ""))
    .filter(Boolean);
  return parts.join(" · ") || "Untitled letter";
};
