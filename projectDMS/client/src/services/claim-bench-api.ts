import { api } from "./api";
import type { ClaimDTO } from "./claims-api";
import type {
  ClaimBenchResponse,
  ClaimSignal,
  DelayView,
  ElementKey,
  ElementState,
  HeadInput,
  HeadPatch,
  LinkCandidate,
  NoticeCandidates,
  NoticeRequirement,
  NoticeRequirementInput,
  Perspective,
  QuantumLineInput,
  QuantumView,
} from "@/types/claimBench";

// Claim Bench and Notice Clock. Mirrors backend routers/claim_bench.py. Every
// board edit carries the board_version it was made against; the server answers
// 409 when the board has moved on.

const bench = (claimId: string) => `/claims/${encodeURIComponent(claimId)}/bench`;

export async function fetchClaimBenchAvailability(): Promise<{ enabled: boolean; rollout_mode: string }> {
  const { data } = await api.get("/claims/bench/availability");
  return data;
}

export async function fetchClaimBench(claimId: string): Promise<ClaimBenchResponse> {
  const { data } = await api.get<ClaimBenchResponse>(bench(claimId));
  return data;
}

export async function openClaimBench(claimId: string): Promise<ClaimBenchResponse> {
  const { data } = await api.post<ClaimBenchResponse>(`${bench(claimId)}/open`);
  return data;
}

export async function addClaimHead(claimId: string, boardVersion: number, head: HeadInput): Promise<ClaimBenchResponse> {
  const { data } = await api.post<ClaimBenchResponse>(`${bench(claimId)}/heads`, {
    board_version: boardVersion,
    ...head,
  });
  return data;
}

export async function patchClaimHead(
  claimId: string,
  headId: string,
  boardVersion: number,
  patch: HeadPatch
): Promise<ClaimBenchResponse> {
  const { data } = await api.patch<ClaimBenchResponse>(`${bench(claimId)}/heads/${encodeURIComponent(headId)}`, {
    board_version: boardVersion,
    ...patch,
  });
  return data;
}

export async function removeClaimHead(claimId: string, headId: string, boardVersion: number): Promise<ClaimBenchResponse> {
  const { data } = await api.post<ClaimBenchResponse>(
    `${bench(claimId)}/heads/${encodeURIComponent(headId)}/remove`,
    { board_version: boardVersion }
  );
  return data;
}

export async function decideElement(
  claimId: string,
  headId: string,
  element: ElementKey,
  boardVersion: number,
  decision: { state?: ElementState; clear?: boolean; note?: string }
): Promise<ClaimBenchResponse> {
  const { data } = await api.put<ClaimBenchResponse>(
    `${bench(claimId)}/heads/${encodeURIComponent(headId)}/elements/${element}`,
    { board_version: boardVersion, ...decision }
  );
  return data;
}

export async function testClaimHead(
  claimId: string,
  headId: string,
  boardVersion: number,
  force = false
): Promise<ClaimBenchResponse> {
  const { data } = await api.post<ClaimBenchResponse>(
    `${bench(claimId)}/heads/${encodeURIComponent(headId)}/test`,
    { board_version: boardVersion, force }
  );
  return data;
}

export async function lockClaimBench(claimId: string, boardVersion: number): Promise<ClaimBenchResponse> {
  const { data } = await api.post<ClaimBenchResponse>(`${bench(claimId)}/lock`, { board_version: boardVersion });
  return data;
}

/** Reopening needs claim-manage rights and a reason, which is kept in the audit trail (D-22). */
export async function unlockClaimBench(claimId: string, boardVersion: number, reason: string): Promise<ClaimBenchResponse> {
  const { data } = await api.post<ClaimBenchResponse>(`${bench(claimId)}/unlock`, { board_version: boardVersion, reason });
  return data;
}

export async function fetchClaimSignals(): Promise<ClaimSignal[]> {
  const { data } = await api.get<ClaimSignal[]>("/claims/bench/signals");
  return Array.isArray(data) ? data : [];
}

export async function raisePotentialClaim(
  sourceType: ClaimSignal["source_type"],
  sourceId: string
): Promise<{ claim: ClaimDTO; created: boolean }> {
  const { data } = await api.post("/claims/bench/potential", { source_type: sourceType, source_id: sourceId });
  return { claim: { ...data.claim, id: data.claim?._id ?? data.claim?.id }, created: Boolean(data.created) };
}

export async function fetchNoticeCandidates(claimType: string, claimId?: string): Promise<NoticeCandidates> {
  const { data } = await api.get<NoticeCandidates>("/claims/notice/candidates", {
    params: { claim_type: claimType, claim_id: claimId },
  });
  return data;
}

export async function setNoticeRequirement(input: NoticeRequirementInput): Promise<NoticeRequirement> {
  const { data } = await api.post<NoticeRequirement>("/claims/notice/requirements", input);
  return data;
}

export async function verifyNoticeRequirement(requirementId: string): Promise<NoticeRequirement> {
  const { data } = await api.post<NoticeRequirement>(
    `/claims/notice/requirements/${encodeURIComponent(requirementId)}/verify`
  );
  return data;
}

export async function setProjectPerspective(
  projectId: string,
  perspective: Perspective,
  reason?: string
): Promise<void> {
  await api.patch(`/projects/${encodeURIComponent(projectId)}/contractual-capacity`, {
    contractual_capacity: perspective,
    ...(reason ? { reason } : {}),
  });
}

/** Which party a project acts for, where that comes from, and whether the signed-in person may set it. */
export interface PerspectiveState {
  value: Perspective | null;
  source: "project" | "organisation_default" | "none";
  in_use: boolean;
  can_set: boolean;
  cannot_set_reason?: string | null;
}

export async function fetchProjectPerspective(projectId: string): Promise<PerspectiveState> {
  const { data } = await api.get<PerspectiveState>(
    `/projects/${encodeURIComponent(projectId)}/contractual-capacity`
  );
  return data;
}

// --------------------------------------------------------------------------- //
// Delay Lab and Quantum Builder
// --------------------------------------------------------------------------- //
const headPath = (claimId: string, headId: string) => `${bench(claimId)}/heads/${encodeURIComponent(headId)}`;

export async function fetchHeadDelay(claimId: string, headId: string): Promise<DelayView> {
  const { data } = await api.get<DelayView>(`${headPath(claimId, headId)}/delay`);
  return data;
}

export async function fetchDelayCandidates(claimId: string): Promise<LinkCandidate[]> {
  const { data } = await api.get<LinkCandidate[]>(`${bench(claimId)}/delay/candidates`);
  return Array.isArray(data) ? data : [];
}

export async function addHeadLink(
  claimId: string,
  headId: string,
  boardVersion: number,
  link: { target_type: string; target_id: string; milestone_ref?: string }
): Promise<DelayView> {
  const { data } = await api.post<DelayView>(`${headPath(claimId, headId)}/delay/links`, {
    board_version: boardVersion,
    ...link,
  });
  return data;
}

export async function removeHeadLink(
  claimId: string,
  headId: string,
  linkId: string,
  boardVersion: number,
  refresh = false
): Promise<DelayView> {
  const { data } = await api.post<DelayView>(
    `${headPath(claimId, headId)}/delay/links/${encodeURIComponent(linkId)}/remove`,
    { board_version: boardVersion, refresh }
  );
  return data;
}

export async function setDelaySettings(
  claimId: string,
  headId: string,
  boardVersion: number,
  settings: {
    method?: string;
    as_of_date?: string;
    clear_as_of_date?: boolean;
    include_neutral?: boolean;
    neutral_basis?: string;
  }
): Promise<DelayView> {
  const { data } = await api.put<DelayView>(`${headPath(claimId, headId)}/delay/settings`, {
    board_version: boardVersion,
    ...settings,
  });
  return data;
}

export async function decideDelayEvent(
  claimId: string,
  headId: string,
  eventId: string,
  boardVersion: number,
  decision: {
    risk_owner?: string;
    criticality?: string;
    clear?: boolean;
    /** Clear one decision and keep the other (4B-25). */
    clear_fields?: ("risk_owner" | "criticality")[];
    note?: string;
  }
): Promise<DelayView> {
  const { data } = await api.put<DelayView>(
    `${headPath(claimId, headId)}/delay/events/${encodeURIComponent(eventId)}`,
    { board_version: boardVersion, ...decision }
  );
  return data;
}

export async function decideDelayOverlap(
  claimId: string,
  headId: string,
  overlapId: string,
  boardVersion: number,
  decision: { treatment?: string; days_to_eot?: number; basis?: string; clear?: boolean }
): Promise<DelayView> {
  const { data } = await api.put<DelayView>(
    `${headPath(claimId, headId)}/delay/overlaps/${encodeURIComponent(overlapId)}`,
    { board_version: boardVersion, ...decision }
  );
  return data;
}

export async function recordOverlapCostBasis(
  claimId: string,
  headId: string,
  overlapId: string,
  boardVersion: number,
  cost: { contractual_basis: string; causation: string; evidence_document_ids: string[] }
): Promise<DelayView> {
  const { data } = await api.put<DelayView>(
    `${headPath(claimId, headId)}/delay/overlaps/${encodeURIComponent(overlapId)}/cost-basis`,
    { board_version: boardVersion, ...cost }
  );
  return data;
}

export async function reviewOverlapCostBasis(
  claimId: string,
  headId: string,
  overlapId: string,
  boardVersion: number
): Promise<DelayView> {
  const { data } = await api.post<DelayView>(
    `${headPath(claimId, headId)}/delay/overlaps/${encodeURIComponent(overlapId)}/cost-basis/review`,
    { board_version: boardVersion }
  );
  return data;
}

export async function fetchHeadQuantum(claimId: string, headId: string): Promise<QuantumView> {
  const { data } = await api.get<QuantumView>(`${headPath(claimId, headId)}/quantum`);
  return data;
}

export async function saveQuantumLine(
  claimId: string,
  headId: string,
  boardVersion: number,
  line: QuantumLineInput,
  lineId?: string
): Promise<QuantumView> {
  const body = { board_version: boardVersion, ...line };
  const { data } = lineId
    ? await api.put<QuantumView>(`${headPath(claimId, headId)}/quantum/lines/${encodeURIComponent(lineId)}`, body)
    : await api.post<QuantumView>(`${headPath(claimId, headId)}/quantum/lines`, body);
  return data;
}

export async function removeQuantumLine(
  claimId: string,
  headId: string,
  lineId: string,
  boardVersion: number
): Promise<QuantumView> {
  const { data } = await api.post<QuantumView>(
    `${headPath(claimId, headId)}/quantum/lines/${encodeURIComponent(lineId)}/remove`,
    { board_version: boardVersion }
  );
  return data;
}
