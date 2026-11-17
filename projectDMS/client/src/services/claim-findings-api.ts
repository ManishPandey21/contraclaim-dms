import { api } from "./api";
import type {
  ClaimSimulation,
  DeterminationView,
  DeterminationsResponse,
  HeadFindingInput,
  SimulationsResponse,
  StrategyNoteInput,
  StrategyNoteResponse,
  SubmissionView,
} from "@/types/claimFindings";
import type { Perspective } from "@/types/claimBench";

// Two Chairs and the Submission Pack. Mirrors backend routers/claim_findings.py.
// An edit to a determination carries the version it was made against; the
// server answers 409 when the determination has moved on.

const base = (claimId: string) => `/claims/${encodeURIComponent(claimId)}`;
const one = (claimId: string, id: string) => `${base(claimId)}/determinations/${encodeURIComponent(id)}`;

export async function fetchDeterminations(claimId: string): Promise<DeterminationsResponse> {
  const { data } = await api.get<DeterminationsResponse>(`${base(claimId)}/determinations`);
  return data;
}

export async function createDetermination(
  claimId: string,
  input:
    | { origin: "authored" }
    | {
        origin: "received";
        issued_by: "engineer" | "employer";
        source_document_id: string;
        reference?: string;
        determination_date?: string | null;
      }
): Promise<DeterminationView> {
  const { data } = await api.post<DeterminationView>(`${base(claimId)}/determinations`, input);
  return data;
}

export async function setHeadFinding(
  claimId: string,
  determinationId: string,
  headId: string,
  version: number,
  finding: HeadFindingInput
): Promise<DeterminationView> {
  const { data } = await api.put<DeterminationView>(
    `${one(claimId, determinationId)}/heads/${encodeURIComponent(headId)}`,
    { version, ...finding }
  );
  return data;
}

export type DeterminationAction = "propose" | "approve" | "return" | "record";

export async function actOnDetermination(
  claimId: string,
  determinationId: string,
  action: DeterminationAction,
  version: number,
  comment?: string
): Promise<DeterminationView> {
  const { data } = await api.post<DeterminationView>(`${one(claimId, determinationId)}/${action}`, { version, comment });
  return data;
}

export async function submitDetermination(
  claimId: string,
  determinationId: string,
  version: number,
  reviewerId: string
): Promise<DeterminationView> {
  const { data } = await api.post<DeterminationView>(`${one(claimId, determinationId)}/submit`, {
    version,
    reviewer_id: reviewerId,
  });
  return data;
}

/** Send a determination in review to another approver: only when its reviewer no longer holds the authority (4B-26). */
export async function reassignDetermination(
  claimId: string,
  determinationId: string,
  version: number,
  reviewerId: string
): Promise<DeterminationView> {
  const { data } = await api.post<DeterminationView>(`${one(claimId, determinationId)}/reassign`, {
    version,
    reviewer_id: reviewerId,
  });
  return data;
}

export async function reviseDetermination(claimId: string, determinationId: string): Promise<DeterminationView> {
  const { data } = await api.post<DeterminationView>(`${one(claimId, determinationId)}/revise`);
  return data;
}

export async function fetchSimulations(claimId: string): Promise<SimulationsResponse> {
  const { data } = await api.get<SimulationsResponse>(`${base(claimId)}/simulations`);
  return data;
}

export async function simulateResponse(claimId: string, party: Perspective): Promise<ClaimSimulation> {
  const { data } = await api.post<ClaimSimulation>(`${base(claimId)}/simulations`, { simulated_party: party });
  return data;
}

export async function fetchStrategyNote(claimId: string): Promise<StrategyNoteResponse> {
  const { data } = await api.get<StrategyNoteResponse>(`${base(claimId)}/strategy-note`);
  return data;
}

export async function saveStrategyNote(
  claimId: string,
  version: number,
  note: StrategyNoteInput
): Promise<StrategyNoteResponse> {
  const { data } = await api.put<StrategyNoteResponse>(`${base(claimId)}/strategy-note`, { version, ...note });
  return data;
}

export async function generateStrategyNote(claimId: string): Promise<StrategyNoteResponse> {
  const { data } = await api.post<StrategyNoteResponse>(`${base(claimId)}/strategy-note/generate`);
  return data;
}

export async function fetchSubmission(claimId: string): Promise<SubmissionView> {
  const { data } = await api.get<SubmissionView>(`${base(claimId)}/submission`);
  return data;
}

export async function composeSubmission(
  claimId: string,
  sections: string[] = [],
  confirmOverwrite: string[] = []
): Promise<SubmissionView> {
  const { data } = await api.post<SubmissionView>(`${base(claimId)}/submission/compose`, {
    sections,
    confirm_overwrite: confirmOverwrite,
  });
  return data;
}

/** 4B-32: new text for each paragraph of one section; sources are kept by the server. */
export async function editSubmissionSection(
  claimId: string,
  sectionKey: string,
  version: number,
  paragraphs: string[]
): Promise<SubmissionView> {
  const { data } = await api.put<SubmissionView>(
    `${base(claimId)}/submission/sections/${encodeURIComponent(sectionKey)}`,
    { version, paragraphs }
  );
  return data;
}

export async function downloadSubmission(claimId: string, format: "docx" | "pdf"): Promise<string> {
  const res = await api.get(`${base(claimId)}/submission/export`, { params: { format }, responseType: "blob" });
  const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
  const disposition = (res.headers as Record<string, string> | undefined)?.["content-disposition"];
  const filename = disposition?.match(/filename="?([^";]+)"?/i)?.[1] ?? `claim-submission.${format}`;
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
  return filename;
}

/** OD-1: one record the claim relies on whose contract a person must confirm. */
export interface EvidenceContractItem {
  kind: string;
  record_id: string;
  label: string;
  state: "unverified" | "unidentified";
  confirmation_id?: string | null;
  confirmed_by?: string | null;
  confirmed_at?: string | null;
  reason?: string | null;
  source_reference?: string | null;
  /** A confirmation that no longer holds because the record changed since. */
  superseded_confirmation_id?: string | null;
  claim_id?: string;
}

export interface EvidenceContractReview {
  claim_id: string;
  contract_id?: string | null;
  conflicts: string[];
  unconfirmed: EvidenceContractItem[];
  confirmed: EvidenceContractItem[];
  blocking?: string | null;
}

/** Confirm that a record belongs to the claim's contract. Audited; the record itself is not changed. */
export async function confirmEvidenceContract(
  claimId: string,
  input: { record_kind: string; record_id: string; reason: string; source_reference: string }
): Promise<EvidenceContractReview> {
  const { data } = await api.post<EvidenceContractReview>(
    `/claims/${encodeURIComponent(claimId)}/evidence-contract/confirmations`,
    input
  );
  return data;
}
