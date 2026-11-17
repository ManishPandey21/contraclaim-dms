import { api } from "./api";
import type {
  AssertionPatch,
  BenchAvailability,
  BenchOpenResponse,
  BenchResponse,
  ChainLinkInput,
  DraftRole,
  EffectiveSkill,
  SkillAdminList,
  SkillBinding,
  SkillApproval,
  SkillApprovalKind,
  SkillApprovalList,
  SkillEvalReport,
  SkillReviewQueue,
  SkillStep,
  SkillVersionSummary,
  TextSpan,
} from "@/types/letterBench";

const bench = (letterId: string) => `/letters/${encodeURIComponent(letterId)}/bench`;

export async function fetchBenchAvailability(): Promise<BenchAvailability> {
  const { data } = await api.get<BenchAvailability>("/letters/bench/availability");
  return data;
}

export async function openBench(payload: {
  incoming_document_id?: string;
  trigger_type?: "variation" | "claim";
  trigger_id?: string;
  role?: DraftRole;
}): Promise<BenchOpenResponse> {
  const { data } = await api.post<BenchOpenResponse>("/letters/bench/open", payload);
  return data;
}

export async function fetchBench(letterId: string): Promise<BenchResponse> {
  const { data } = await api.get<BenchResponse>(bench(letterId));
  return data;
}

export async function dissectLetter(letterId: string): Promise<void> {
  await api.post(`${bench(letterId)}/dissect`);
}

export async function patchAssertion(
  letterId: string,
  assertionId: string,
  boardVersion: number,
  patch: AssertionPatch
): Promise<BenchResponse> {
  const { data } = await api.patch<BenchResponse>(
    `${bench(letterId)}/assertions/${encodeURIComponent(assertionId)}`,
    { ...patch, board_version: boardVersion }
  );
  return data;
}

export async function createAssertion(
  letterId: string,
  boardVersion: number,
  span: TextSpan
): Promise<BenchResponse> {
  const { data } = await api.post<BenchResponse>(`${bench(letterId)}/assertions`, {
    board_version: boardVersion,
    span,
  });
  return data;
}

export async function updateChain(
  letterId: string,
  boardVersion: number,
  links: ChainLinkInput[]
): Promise<BenchResponse> {
  const { data } = await api.put<BenchResponse>(`${bench(letterId)}/chain`, {
    board_version: boardVersion,
    links,
  });
  return data;
}

export async function answerQuestion(
  letterId: string,
  boardVersion: number,
  questionId: string,
  answer: string
): Promise<BenchResponse> {
  const { data } = await api.put<BenchResponse>(`${bench(letterId)}/questions`, {
    board_version: boardVersion,
    question_id: questionId,
    answer,
  });
  return data;
}

export async function updateBenchSettings(
  letterId: string,
  boardVersion: number,
  settings: { role?: DraftRole; deadline_date?: string; deadline_basis?: string }
): Promise<BenchResponse> {
  const { data } = await api.put<BenchResponse>(`${bench(letterId)}/settings`, {
    ...settings,
    board_version: boardVersion,
  });
  return data;
}

export async function patchParagraph(
  letterId: string,
  paragraphId: string,
  boardVersion: number,
  text: string
): Promise<BenchResponse> {
  const { data } = await api.patch<BenchResponse>(
    `${bench(letterId)}/paragraphs/${encodeURIComponent(paragraphId)}`,
    { board_version: boardVersion, text }
  );
  return data;
}

export async function runSkillStep(
  letterId: string,
  payload: { skill_key: string; target_assertion_ids?: string[]; force?: boolean }
): Promise<void> {
  await api.post(`${bench(letterId)}/steps`, {
    target_assertion_ids: [],
    force: false,
    ...payload,
  });
}

export async function fetchSkillStep(letterId: string, stepId: string): Promise<SkillStep> {
  const { data } = await api.get<SkillStep>(`${bench(letterId)}/steps/${encodeURIComponent(stepId)}`);
  return data;
}

export async function lockPositions(letterId: string, boardVersion: number): Promise<BenchResponse> {
  const { data } = await api.post<BenchResponse>(`${bench(letterId)}/lock-positions`, {
    board_version: boardVersion,
  });
  return data;
}

export async function composeReply(letterId: string, boardVersion: number): Promise<void> {
  await api.post(`${bench(letterId)}/compose`, { board_version: boardVersion });
}

export async function submitBenchDraftForReview(letterId: string, draftRunId: string): Promise<void> {
  await api.post(`/letters/${encodeURIComponent(letterId)}/submit`, { draft_run_id: draftRunId });
}

// ---------------------------------------------------------------- skills
/** Skills that can be applied on this letter. Authorised by access to the letter, like the board. */
export async function fetchBenchSkills(letterId: string): Promise<EffectiveSkill[]> {
  const { data } = await api.get<EffectiveSkill[]>(`${bench(letterId)}/skills`);
  return data;
}

export async function fetchSkillAdminList(organizationId?: string): Promise<SkillAdminList> {
  const { data } = await api.get<SkillAdminList>("/skills/admin", {
    params: { organization_id: organizationId },
  });
  return data;
}

export async function importSkillBundle(input: {
  file: File;
  version: string;
  kind: string;
  manifest: string;
}): Promise<SkillVersionSummary> {
  const form = new FormData();
  form.append("file", input.file);
  form.append("version", input.version);
  form.append("kind", input.kind);
  form.append("manifest", input.manifest);
  const { data } = await api.post<SkillVersionSummary>("/skills/import", form);
  return data;
}

export async function publishSkillVersion(
  skillKey: string,
  version: string,
  evalReport: SkillEvalReport
): Promise<SkillVersionSummary> {
  const { data } = await api.post<SkillVersionSummary>(
    `/skills/${encodeURIComponent(skillKey)}/versions/${encodeURIComponent(version)}/publish`,
    { eval_report: evalReport }
  );
  return data;
}

/** Drafts that ask a review from a role the signed-in user holds at platform scope. */
export async function fetchSkillReviewQueue(): Promise<SkillReviewQueue> {
  const { data } = await api.get<SkillReviewQueue>("/skills/review-queue");
  return data;
}

/** One version with its text. A draft is readable only by a reviewer it names. */
export async function fetchSkillVersionText(skillKey: string, version: string): Promise<{ skill_md: string }> {
  const { data } = await api.get<{ skill_md: string }>(versionPath(skillKey, version));
  return data;
}

const versionPath = (skillKey: string, version: string) =>
  `/skills/${encodeURIComponent(skillKey)}/versions/${encodeURIComponent(version)}`;

export async function fetchSkillApprovals(skillKey: string, version: string): Promise<SkillApprovalList> {
  const { data } = await api.get<SkillApprovalList>(`${versionPath(skillKey, version)}/approvals`);
  return data;
}

/** Record the signed-in user's approval. The server checks they hold `reviewerRole`. */
export async function recordSkillApproval(
  skillKey: string,
  version: string,
  input: { kind: SkillApprovalKind; reviewer_role: string; eval_report: SkillEvalReport; comment?: string }
): Promise<SkillApproval> {
  const { data } = await api.post<SkillApproval>(`${versionPath(skillKey, version)}/approvals`, input);
  return data;
}

export async function revokeSkillApproval(approvalId: string, reason: string): Promise<SkillApproval> {
  const { data } = await api.post<SkillApproval>(
    `/skills/approvals/${encodeURIComponent(approvalId)}/revoke`,
    { reason }
  );
  return data;
}

export async function deprecateSkillVersion(skillKey: string, version: string): Promise<SkillVersionSummary> {
  const { data } = await api.post<SkillVersionSummary>(
    `/skills/${encodeURIComponent(skillKey)}/versions/${encodeURIComponent(version)}/deprecate`
  );
  return data;
}

export async function setSkillBinding(binding: {
  organization_id: string;
  project_id?: string | null;
  skill_key: string;
  pinned_version?: string | null;
  enabled: boolean;
}): Promise<SkillBinding> {
  const { data } = await api.put<SkillBinding>("/skills/bindings", binding);
  return data;
}
