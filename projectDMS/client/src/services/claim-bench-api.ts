import { api } from "./api";
import type { ClaimDTO } from "./claims-api";
import type {
  ClaimBenchResponse,
  ClaimSignal,
  ElementKey,
  ElementState,
  HeadInput,
  HeadPatch,
  NoticeCandidates,
  NoticeRequirement,
  NoticeRequirementInput,
  Perspective,
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

export async function unlockClaimBench(claimId: string, boardVersion: number): Promise<ClaimBenchResponse> {
  const { data } = await api.post<ClaimBenchResponse>(`${bench(claimId)}/unlock`, { board_version: boardVersion });
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

export async function setProjectPerspective(projectId: string, perspective: Perspective): Promise<void> {
  await api.patch(`/projects/${encodeURIComponent(projectId)}/contractual-capacity`, {
    contractual_capacity: perspective,
  });
}
