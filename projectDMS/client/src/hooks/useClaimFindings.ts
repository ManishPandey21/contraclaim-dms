import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { errorMessage } from "@/hooks/useLetterBench";
import {
  actOnDetermination,
  composeSubmission,
  createDetermination,
  downloadSubmission,
  fetchDeterminations,
  fetchSimulations,
  fetchStrategyNote,
  fetchSubmission,
  generateStrategyNote,
  reassignDetermination,
  reviseDetermination,
  saveStrategyNote,
  setHeadFinding,
  simulateResponse,
  submitDetermination,
  type DeterminationAction,
} from "@/services/claim-findings-api";
import type { Perspective } from "@/types/claimBench";
import type { HeadFindingInput, StrategyNoteInput } from "@/types/claimFindings";

export const findingsKeys = {
  determinations: (claimId: string) => ["claim-findings", "determinations", claimId] as const,
  simulations: (claimId: string) => ["claim-findings", "simulations", claimId] as const,
  strategy: (claimId: string) => ["claim-findings", "strategy", claimId] as const,
  submission: (claimId: string) => ["claim-findings", "submission", claimId] as const,
};

/** Determinations of a claim. Every change reloads the list: a refused edit usually means it moved on. */
export function useDeterminations(claimId: string) {
  const client = useQueryClient();
  const key = findingsKeys.determinations(claimId);
  const query = useQuery({ queryKey: key, queryFn: () => fetchDeterminations(claimId) });
  const reload = () => client.invalidateQueries({ queryKey: key });
  const change = <TInput,>(run: (input: TInput) => Promise<unknown>, fallback: string, done?: string) =>
    // eslint-disable-next-line react-hooks/rules-of-hooks
    useMutation({
      mutationFn: run,
      onSuccess: async () => {
        if (done) toast.success(done);
        await reload();
      },
      onError: async (error: unknown) => {
        toast.error(errorMessage(error, fallback));
        await reload();
      },
    });

  const create = change(
    (input: Parameters<typeof createDetermination>[1]) => createDetermination(claimId, input),
    "The determination could not be started"
  );
  const setFinding = change(
    (input: { id: string; headId: string; version: number; finding: HeadFindingInput }) =>
      setHeadFinding(claimId, input.id, input.headId, input.version, input.finding),
    "The finding could not be saved",
    "Finding saved"
  );
  const act = change(
    (input: { id: string; action: DeterminationAction; version: number; comment?: string }) =>
      actOnDetermination(claimId, input.id, input.action, input.version, input.comment),
    "That could not be done"
  );
  const submit = change(
    (input: { id: string; version: number; reviewerId: string }) =>
      submitDetermination(claimId, input.id, input.version, input.reviewerId),
    "The determination could not be submitted",
    "Submitted for approval"
  );
  const reassign = change(
    (input: { id: string; version: number; reviewerId: string }) =>
      reassignDetermination(claimId, input.id, input.version, input.reviewerId),
    "The determination could not be sent to another approver",
    "Sent to another approver"
  );
  const revise = change((id: string) => reviseDetermination(claimId, id), "A new revision could not be started");
  return { query, create, setFinding, act, submit, reassign, revise };
}

/** Privileged. Only mounted for a person who holds access to it; the server refuses anyone else. */
export function useInternalAnalysis(claimId: string) {
  const client = useQueryClient();
  const simulations = useQuery({
    queryKey: findingsKeys.simulations(claimId),
    queryFn: () => fetchSimulations(claimId),
    retry: false,
  });
  const strategy = useQuery({
    queryKey: findingsKeys.strategy(claimId),
    queryFn: () => fetchStrategyNote(claimId),
    retry: false,
  });
  const simulate = useMutation({
    mutationFn: (party: Perspective) => simulateResponse(claimId, party),
    onSuccess: () => client.invalidateQueries({ queryKey: findingsKeys.simulations(claimId) }),
    onError: (error: unknown) => toast.error(errorMessage(error, "The simulation could not be run")),
  });
  const onNote = {
    onSuccess: (data: Awaited<ReturnType<typeof fetchStrategyNote>>) => {
      client.setQueryData(findingsKeys.strategy(claimId), data);
    },
    onError: async (error: unknown) => {
      toast.error(errorMessage(error, "The strategy note could not be saved"));
      await client.invalidateQueries({ queryKey: findingsKeys.strategy(claimId) });
    },
  };
  const generate = useMutation({ mutationFn: () => generateStrategyNote(claimId), ...onNote });
  const save = useMutation({
    mutationFn: (input: { version: number; note: StrategyNoteInput }) => saveStrategyNote(claimId, input.version, input.note),
    ...onNote,
  });
  return { simulations, strategy, simulate, generate, save };
}

export function useSubmission(claimId: string) {
  const client = useQueryClient();
  const key = findingsKeys.submission(claimId);
  const query = useQuery({ queryKey: key, queryFn: () => fetchSubmission(claimId) });
  const compose = useMutation({
    mutationFn: (sections: string[]) => composeSubmission(claimId, sections),
    onSuccess: (data) => client.setQueryData(key, data),
    onError: async (error: unknown) => {
      toast.error(errorMessage(error, "The submission could not be composed"));
      await client.invalidateQueries({ queryKey: key });
    },
  });
  const download = useMutation({
    mutationFn: (format: "docx" | "pdf") => downloadSubmission(claimId, format),
    onSuccess: (filename) => toast.success(`Downloaded ${filename}`),
    onError: async (error: unknown) => {
      toast.error(errorMessage(error, "The submission could not be exported"));
      await client.invalidateQueries({ queryKey: key });
    },
  });
  return { query, compose, download };
}
