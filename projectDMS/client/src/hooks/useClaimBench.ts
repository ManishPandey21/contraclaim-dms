import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { toast } from "sonner";

import { errorMessage } from "@/hooks/useLetterBench";
import {
  addClaimHead,
  decideElement,
  fetchClaimBench,
  fetchClaimBenchAvailability,
  fetchClaimSignals,
  lockClaimBench,
  openClaimBench,
  patchClaimHead,
  removeClaimHead,
  setProjectPerspective,
  testClaimHead,
  unlockClaimBench,
} from "@/services/claim-bench-api";
import type {
  ClaimBenchResponse,
  ElementKey,
  ElementState,
  HeadInput,
  HeadPatch,
  Perspective,
} from "@/types/claimBench";

export const claimBenchKeys = {
  availability: ["claim-bench", "availability"] as const,
  bench: (claimId: string) => ["claim-bench", "board", claimId] as const,
  signals: ["claim-bench", "signals"] as const,
};

/** The server decides whether the Claim Bench is on. */
export function useClaimBenchAvailability() {
  return useQuery({
    queryKey: claimBenchKeys.availability,
    queryFn: fetchClaimBenchAvailability,
    staleTime: 60_000,
    retry: false,
  });
}

export function useClaimSignals(enabled: boolean) {
  return useQuery({ queryKey: claimBenchKeys.signals, queryFn: fetchClaimSignals, enabled, retry: false });
}

const isConflict = (error: unknown) => isAxiosError(error) && error.response?.status === 409;

/**
 * The board and every edit to it. Each edit carries the board_version on
 * screen; when the server answers 409 the board is reloaded and the person is
 * told why, so a second editor's work is never silently overwritten.
 */
export function useClaimBench(claimId: string) {
  const client = useQueryClient();
  const key = claimBenchKeys.bench(claimId);
  const query = useQuery({ queryKey: key, queryFn: () => fetchClaimBench(claimId), enabled: Boolean(claimId) });
  const version = query.data?.bench.board_version ?? 0;

  const settle = (data: ClaimBenchResponse) => client.setQueryData(key, data);
  const fail = (fallback: string) => (error: unknown) => {
    toast.error(errorMessage(error, fallback));
    if (isConflict(error)) void client.invalidateQueries({ queryKey: key });
  };
  const edit = <TInput,>(run: (input: TInput) => Promise<ClaimBenchResponse>, fallback: string, done?: string) =>
    // eslint-disable-next-line react-hooks/rules-of-hooks
    useMutation({
      mutationFn: run,
      onSuccess: (data) => {
        settle(data);
        if (done) toast.success(done);
      },
      onError: fail(fallback),
    });

  const open = edit<void>(() => openClaimBench(claimId), "The Bench could not be opened");
  const addHead = edit<HeadInput>((head) => addClaimHead(claimId, version, head), "The head could not be added", "Head added");
  const patchHead = edit<{ headId: string; patch: HeadPatch }>(
    ({ headId, patch }) => patchClaimHead(claimId, headId, version, patch),
    "The head could not be saved"
  );
  const removeHead = edit<string>((headId) => removeClaimHead(claimId, headId, version), "The head could not be removed", "Head removed");
  const decide = edit<{ headId: string; element: ElementKey; state?: ElementState; clear?: boolean; note?: string }>(
    ({ headId, element, ...decision }) => decideElement(claimId, headId, element, version, decision),
    "Your call could not be saved"
  );
  const test = edit<{ headId: string; force?: boolean }>(
    ({ headId, force }) => testClaimHead(claimId, headId, version, force),
    "The head could not be tested"
  );
  const lock = edit<void>(() => lockClaimBench(claimId, version), "The board could not be locked", "Board locked");
  const unlock = edit<void>(() => unlockClaimBench(claimId, version), "The board could not be reopened", "Board reopened");

  const perspective = useMutation({
    mutationFn: (value: Perspective) => {
      const projectId = query.data?.project_id;
      if (!projectId) throw new Error("This claim has no project, so its party cannot be recorded.");
      return setProjectPerspective(projectId, value);
    },
    onSuccess: () => {
      toast.success("Recorded for this project");
      void client.invalidateQueries({ queryKey: key });
    },
    onError: (error) =>
      toast.error(errorMessage(error, "This could not be recorded. A project administrator can set it.")),
  });

  return { query, open, addHead, patchHead, removeHead, decide, test, lock, unlock, perspective };
}
