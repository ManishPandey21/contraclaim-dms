import { api } from "./api";
import type { DocumentItem } from "./documents-api";

export type DocumentRelationshipRole =
  | "notice"
  | "claim_submission"
  | "supporting_document"
  | "engineer_response"
  | "employer_response"
  | "determination"
  | "correspondence"
  | "ipc_submission"
  | "certified_ipc"
  | "invoice"
  | "payment_certificate"
  | "payment_correspondence"
  | "original_bg"
  | "submission"
  | "extension"
  | "release"
  | "contractor_notification"
  | "engineer_acknowledgement"
  | "completion_certificate"
  | "inspection_record"
  | "eot_submission"
  | "eot_supporting_document"
  | "eot_determination"
  | "engineer_determination"
  | "policy"
  | "certificate"
  | "variation_submission"
  | "variation_approval";

export { CORRESPONDENCE_ROLES, isCorrespondenceRole } from "@/lib/relationship-roles";

export const VARIATION_DOCUMENT_RELATIONSHIP_ROLES: ReadonlyArray<{
  value: DocumentRelationshipRole;
  label: string;
}> = [
  { value: "correspondence", label: "Correspondence" },
  { value: "variation_submission", label: "Variation submission" },
  { value: "variation_approval", label: "Variation approval" },
  { value: "supporting_document", label: "Supporting document" },
];

export const INSURANCE_DOCUMENT_RELATIONSHIP_ROLES: ReadonlyArray<{
  value: DocumentRelationshipRole;
  label: string;
}> = [
  { value: "policy", label: "Policy" },
  { value: "certificate", label: "Certificate" },
  { value: "correspondence", label: "Correspondence" },
  { value: "supporting_document", label: "Supporting document" },
];

export const KEY_DATE_ACHIEVEMENT_RELATIONSHIP_ROLES: ReadonlyArray<{
  value: DocumentRelationshipRole;
  label: string;
}> = [
  { value: "contractor_notification", label: "Contractor notification" },
  { value: "engineer_acknowledgement", label: "Engineer acknowledgement" },
  { value: "completion_certificate", label: "Completion certificate" },
  { value: "inspection_record", label: "Inspection record" },
  { value: "supporting_document", label: "Supporting document" },
  { value: "correspondence", label: "Correspondence" },
];

export const EOT_SUBMISSION_RELATIONSHIP_ROLES: ReadonlyArray<{
  value: DocumentRelationshipRole;
  label: string;
}> = [
  { value: "eot_submission", label: "EOT submission" },
  { value: "eot_supporting_document", label: "EOT supporting document" },
  { value: "supporting_document", label: "Supporting document" },
  { value: "correspondence", label: "Correspondence" },
];

export const EOT_DETERMINATION_RELATIONSHIP_ROLES: ReadonlyArray<{
  value: DocumentRelationshipRole;
  label: string;
}> = [
  { value: "eot_determination", label: "EOT determination" },
  { value: "engineer_determination", label: "Engineer determination" },
  { value: "supporting_document", label: "Supporting document" },
  { value: "correspondence", label: "Correspondence" },
];

export const CLAIM_DOCUMENT_RELATIONSHIP_ROLES: ReadonlyArray<{
  value: DocumentRelationshipRole;
  label: string;
}> = [
  { value: "notice", label: "Notice" },
  { value: "claim_submission", label: "Claim submission" },
  { value: "supporting_document", label: "Supporting document" },
  { value: "engineer_response", label: "Engineer response" },
  { value: "employer_response", label: "Employer response" },
  { value: "determination", label: "Determination" },
  { value: "correspondence", label: "Correspondence" },
];

export const IPC_DOCUMENT_RELATIONSHIP_ROLES: ReadonlyArray<{
  value: DocumentRelationshipRole;
  label: string;
}> = [
  { value: "ipc_submission", label: "IPC submission" },
  { value: "certified_ipc", label: "Certified IPC" },
  { value: "invoice", label: "Invoice" },
  { value: "payment_certificate", label: "Payment certificate" },
  { value: "supporting_document", label: "Supporting document" },
  { value: "payment_correspondence", label: "Payment correspondence" },
];

export const BANK_GUARANTEE_EVENT_RELATIONSHIP_ROLES: Readonly<Record<
  "original" | "submission" | "extension" | "release",
  ReadonlyArray<{ value: DocumentRelationshipRole; label: string }>
>> = {
  original: [
    { value: "original_bg", label: "Original BG" },
    { value: "supporting_document", label: "Supporting document" },
    { value: "correspondence", label: "Correspondence" },
  ],
  submission: [
    { value: "submission", label: "Submission" },
    { value: "original_bg", label: "Original BG" },
    { value: "supporting_document", label: "Supporting document" },
    { value: "correspondence", label: "Correspondence" },
  ],
  extension: [
    { value: "extension", label: "Extension" },
    { value: "supporting_document", label: "Supporting document" },
    { value: "correspondence", label: "Correspondence" },
  ],
  release: [
    { value: "release", label: "Release" },
    { value: "supporting_document", label: "Supporting document" },
    { value: "correspondence", label: "Correspondence" },
  ],
};

export interface DocumentRelationship {
  _id: string;
  document_id: string;
  document_version_id?: string | null;
  relationship_role: string;
  description?: string | null;
  source?: string;
  _revision: number;
  frozen_at?: string | null;
  document?: DocumentItem | null;
  target_type?: string;
  target_id?: string;
  target_label?: string | null;
  target_route?: string | null;
}

export async function listEntityDocumentLinks(targetType: string, targetId: string) {
  const { data } = await api.get(
    `/entities/${encodeURIComponent(targetType)}/${encodeURIComponent(targetId)}/document-links`,
  );
  return (data?.links ?? []) as DocumentRelationship[];
}

export async function batchLinkDocuments(
  targetType: string,
  targetId: string,
  links: Array<{ document_id: string; relationship_role: string }>,
) {
  const { data } = await api.post(
    `/entities/${encodeURIComponent(targetType)}/${encodeURIComponent(targetId)}/document-links:batch`,
    { links },
  );
  return (data?.links ?? []) as DocumentRelationship[];
}

export async function removeDocumentLink(
  linkId: string,
  expectedRevision: number,
  reason: string,
) {
  const { data } = await api.post(`/document-links/${encodeURIComponent(linkId)}:remove`, {
    expected_revision: expectedRevision,
    reason,
  });
  return data?.link as DocumentRelationship;
}

export type LinkableUploadType = "incoming" | "outgoing" | "contract" | "correspondence";

export async function searchLinkableDocuments(params: {
  q?: string;
  organization_id?: string;
  project_id?: string;
  /** "correspondence" matches incoming or outgoing Documents only. */
  uploadType?: LinkableUploadType;
  /** Exact letter number (case-insensitive on the server). */
  letterNo?: string;
  /** Subject contains (case-insensitive). */
  subject?: string;
  /** ISO dates (YYYY-MM-DD), inclusive, on the Document date. */
  date_from?: string;
  date_to?: string;
  limit?: number;
  skip?: number;
}) {
  const { data } = await api.get("/document-search", {
    params: { limit: 25, skip: 0, ...params },
  });
  return (data?.documents ?? []) as DocumentItem[];
}

export async function listDocumentEntityLinks(documentId: string) {
  const { data } = await api.get(
    `/documents/${encodeURIComponent(documentId)}/entity-links`,
  );
  return (data?.links ?? []) as DocumentRelationship[];
}

/** A register record the caller may link a Document to (server: link_targets_for_document). */
export interface DocumentLinkTarget {
  target_type: string;
  target_id: string;
  label: string;
  route: string;
  allowed_roles: string[];
  frozen: boolean;
  parent_type?: string | null;
  parent_id?: string | null;
}

export async function listDocumentLinkTargets(
  documentId: string,
  params: { target_type: string; q?: string; limit?: number },
) {
  const { data } = await api.get(
    `/documents/${encodeURIComponent(documentId)}/link-targets`,
    { params },
  );
  return (data?.targets ?? []) as DocumentLinkTarget[];
}
