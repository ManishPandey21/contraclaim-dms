import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { toast } from "sonner";

import { applyOptimisticPatch, isBenchBusy } from "@/components/letter-bench/benchLogic";
import {
  answerQuestion,
  composeReply,
  createAssertion,
  dissectLetter,
  fetchBench,
  fetchBenchAvailability,
  fetchSkillStep,
  fetchSkills,
  lockPositions,
  openBench,
  patchAssertion,
  patchParagraph,
  runSkillStep,
  submitBenchDraftForReview,
  updateBenchSettings,
  updateChain,
} from "@/services/letter-bench-api";
import type {
  AssertionPatch,
  BenchResponse,
  ChainLinkInput,
  DraftRole,
  TextSpan,
} from "@/types/letterBench";

export const benchKeys = {
  availability: ["letter-bench", "availability"] as const,
  bench: (letterId: string) => ["letter-bench", "board", letterId] as const,
  step: (letterId: string, stepId: string) => ["letter-bench", "step", letterId, stepId] as const,
  skills: (organizationId?: string, projectId?: string | null) =>
    ["letter-bench", "skills", organizationId ?? "", projectId ?? ""] as const,
};

const CLIENT_FLAG_OFF = String(import.meta.env?.VITE_LETTER_BENCH_ENABLED ?? "").toLowerCase() === "false";
const POLL_MS = 1500;

export function errorMessage(error: unknown, fallback: string): string {
  if (isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

function isConflict(error: unknown): boolean {
  return isAxiosError(error) && error.response?.status === 409;
}

/** The server decides whether the Bench is on; the client flag can only hide it. */
export function useBenchAvailability() {
  const query = useQuery({
    queryKey: benchKeys.availability,
    queryFn: fetchBenchAvailability,
    staleTime: 5 * 60_000,
    retry: false,
    enabled: !CLIENT_FLAG_OFF,
  });
  return {
    enabled: !CLIENT_FLAG_OFF && Boolean(query.data?.enabled),
    isLoading: !CLIENT_FLAG_OFF && query.isLoading,
  };
}

export function useBench(letterId: string | undefined, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: benchKeys.bench(letterId ?? ""),
    queryFn: () => fetchBench(letterId as string),
    enabled: Boolean(letterId) && (options.enabled ?? true),
    retry: false,
    // Poll only while a skill is running; otherwise the board is quiet.
    refetchInterval: (query) => (isBenchBusy(query.state.data) ? POLL_MS : false),
  });
}

export function useSkills(organizationId?: string, projectId?: string | null) {
  return useQuery({
    queryKey: benchKeys.skills(organizationId, projectId),
    queryFn: () => fetchSkills({ organization_id: organizationId, project_id: projectId }),
    enabled: Boolean(organizationId),
    staleTime: 5 * 60_000,
  });
}

export function useSkillStep(letterId: string | undefined, stepId: string | null) {
  return useQuery({
    queryKey: benchKeys.step(letterId ?? "", stepId ?? ""),
    queryFn: () => fetchSkillStep(letterId as string, stepId as string),
    enabled: Boolean(letterId && stepId),
  });
}

export function useOpenBench() {
  return useMutation({ mutationFn: openBench });
}

/**
 * Board edits. Every edit carries the board_version the server last told us.
 * If someone (or a finishing skill) changed the board first, the server answers
 * 409; we refetch and replay the user's action once against the fresh version.
 * Edits for one letter run serially so two quick clicks cannot race each other.
 */
export function useBenchActions(letterId: string) {
  const client = useQueryClient();
  const key = benchKeys.bench(letterId);
  const scope = { id: `letter-bench-${letterId}` };

  const currentVersion = useCallback(async (): Promise<number> => {
    const cached = client.getQueryData<BenchResponse>(key);
    if (cached) return cached.board.board_version;
    const fresh = await client.fetchQuery({ queryKey: key, queryFn: () => fetchBench(letterId) });
    return fresh.board.board_version;
  }, [client, key, letterId]);

  const withReplay = useCallback(
    async (send: (version: number) => Promise<BenchResponse>): Promise<BenchResponse> => {
      try {
        return await send(await currentVersion());
      } catch (error) {
        if (!isConflict(error)) throw error;
        const fresh = await fetchBench(letterId);
        client.setQueryData(key, fresh);
        return send(fresh.board.board_version);
      }
    },
    [client, currentVersion, key, letterId]
  );

  const accept = useCallback(
    (data: BenchResponse) => client.setQueryData(key, data),
    [client, key]
  );
  const refresh = useCallback(() => client.invalidateQueries({ queryKey: key }), [client, key]);
  const fail = (fallback: string) => (error: unknown) => {
    toast.error(errorMessage(error, fallback));
    void refresh();
  };

  const patch = useMutation({
    scope,
    mutationFn: ({ assertionId, patch: change }: { assertionId: string; patch: AssertionPatch }) =>
      withReplay((version) => patchAssertion(letterId, assertionId, version, change)),
    onMutate: async ({ assertionId, patch: change }) => {
      await client.cancelQueries({ queryKey: key });
      const previous = client.getQueryData<BenchResponse>(key);
      if (previous) client.setQueryData(key, applyOptimisticPatch(previous, assertionId, change));
      return { previous };
    },
    onSuccess: accept,
    onError: (error, _variables, context) => {
      if (context?.previous) client.setQueryData(key, context.previous);
      fail("That change could not be saved")(error);
    },
  });

  const addAssertion = useMutation({
    scope,
    mutationFn: (span: TextSpan) => withReplay((version) => createAssertion(letterId, version, span)),
    onSuccess: accept,
    onError: fail("The assertion could not be added"),
  });

  const setChain = useMutation({
    scope,
    mutationFn: (links: ChainLinkInput[]) => withReplay((version) => updateChain(letterId, version, links)),
    onSuccess: accept,
    onError: fail("The chain could not be changed"),
  });

  const answer = useMutation({
    scope,
    mutationFn: ({ questionId, text }: { questionId: string; text: string }) =>
      withReplay((version) => answerQuestion(letterId, version, questionId, text)),
    onSuccess: accept,
    onError: fail("The answer could not be saved"),
  });

  const settings = useMutation({
    scope,
    mutationFn: (change: { role?: DraftRole; deadline_date?: string; deadline_basis?: string }) =>
      withReplay((version) => updateBenchSettings(letterId, version, change)),
    onSuccess: accept,
    onError: fail("The setting could not be saved"),
  });

  const editParagraph = useMutation({
    scope,
    mutationFn: ({ paragraphId, text }: { paragraphId: string; text: string }) =>
      withReplay((version) => patchParagraph(letterId, paragraphId, version, text)),
    onSuccess: accept,
    onError: fail("The paragraph could not be saved"),
  });

  const lock = useMutation({
    scope,
    mutationFn: () => withReplay((version) => lockPositions(letterId, version)),
    onSuccess: (data) => {
      accept(data);
      toast.success("Positions locked");
    },
    onError: fail("Positions could not be locked"),
  });

  const compose = useMutation({
    scope,
    mutationFn: async () => {
      try {
        await composeReply(letterId, await currentVersion());
      } catch (error) {
        if (!isConflict(error)) throw error;
        const fresh = await fetchBench(letterId);
        client.setQueryData(key, fresh);
        if (!fresh.gate.can_compose) throw error;
        await composeReply(letterId, fresh.board.board_version);
      }
    },
    onSuccess: refresh,
    onError: fail("The reply could not be composed"),
  });

  const runStep = useMutation({
    scope,
    mutationFn: (payload: { skill_key: string; target_assertion_ids?: string[]; force?: boolean }) =>
      runSkillStep(letterId, payload),
    onSuccess: refresh,
    onError: fail("The skill could not be run"),
  });

  const dissect = useMutation({
    scope,
    mutationFn: () => dissectLetter(letterId),
    onSuccess: refresh,
    onError: fail("The analysis could not be started"),
  });

  const submit = useMutation({
    mutationFn: (draftRunId: string) => submitBenchDraftForReview(letterId, draftRunId),
    onSuccess: refresh,
    onError: fail("The draft could not be submitted for review"),
  });

  return { patch, addAssertion, setChain, answer, settings, editParagraph, lock, compose, runStep, dissect, submit };
}

export type BenchActions = ReturnType<typeof useBenchActions>;
