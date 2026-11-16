import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { toast } from "sonner";

import { useOptionalTenant } from "@/contexts/TenantContext";
import { errorMessage } from "@/hooks/useLetterBench";
import {
  addClaimHead,
  addHeadLink,
  decideDelayEvent,
  decideDelayOverlap,
  recordOverlapCostBasis,
  reviewOverlapCostBasis,
  decideElement,
  fetchHeadDelay,
  fetchHeadQuantum,
  removeHeadLink,
  removeQuantumLine,
  saveQuantumLine,
  setDelaySettings,
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
  DelayView,
  ElementKey,
  ElementState,
  HeadInput,
  HeadPatch,
  Perspective,
  QuantumLineInput,
  QuantumView,
} from "@/types/claimBench";

/**
 * Availability and signals depend on the navbar selection: the rollout can be
 * on for one organisation and off for another, and signals are listed for the
 * selected project. Their keys therefore carry the selection, so switching
 * organisation never serves the previous one's cached answer.
 */
export const claimBenchKeys = {
  availabilityAll: ["claim-bench", "availability"] as const,
  availability: (organizationId: string) => ["claim-bench", "availability", organizationId] as const,
  bench: (claimId: string) => ["claim-bench", "board", claimId] as const,
  signalsAll: ["claim-bench", "signals"] as const,
  signals: (organizationId: string, projectId: string) =>
    ["claim-bench", "signals", organizationId, projectId] as const,
};

/** The navbar selection, or empty strings outside a tenant provider. */
export function useClaimBenchSelection(): { organizationId: string; projectId: string } {
  const tenant = useOptionalTenant();
  return {
    organizationId: tenant?.selectedOrganizationId ?? "",
    projectId: tenant?.selectedProjectId ?? "",
  };
}

/** The server decides whether the Claim Bench is on. */
export function useClaimBenchAvailability() {
  const { organizationId } = useClaimBenchSelection();
  return useQuery({
    queryKey: claimBenchKeys.availability(organizationId),
    queryFn: fetchClaimBenchAvailability,
    staleTime: 60_000,
    retry: false,
  });
}

export function useClaimSignals(enabled: boolean) {
  const { organizationId, projectId } = useClaimBenchSelection();
  return useQuery({
    queryKey: claimBenchKeys.signals(organizationId, projectId),
    queryFn: fetchClaimSignals,
    enabled,
    retry: false,
  });
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

export const headKeys = {
  delay: (claimId: string, headId: string) => ["claim-bench", "delay", claimId, headId] as const,
  quantum: (claimId: string, headId: string) => ["claim-bench", "quantum", claimId, headId] as const,
  candidates: (claimId: string) => ["claim-bench", "delay-candidates", claimId] as const,
};

/**
 * The delay assessment and quantum build-up of one head. Each edit is made
 * against the board version on screen. After an edit the board is reloaded
 * before the next one is accepted, because the edit moved the board on.
 */
export function useHeadAnalysis(claimId: string, headId: string, boardVersion: number) {
  const client = useQueryClient();
  const delayKey = headKeys.delay(claimId, headId);
  const quantumKey = headKeys.quantum(claimId, headId);
  const delay = useQuery({ queryKey: delayKey, queryFn: () => fetchHeadDelay(claimId, headId) });
  const quantum = useQuery({ queryKey: quantumKey, queryFn: () => fetchHeadQuantum(claimId, headId) });

  const afterEdit = async () => {
    // Quantum locks depend on delay decisions, and both moved the board version.
    await Promise.all([
      client.invalidateQueries({ queryKey: claimBenchKeys.bench(claimId) }),
      client.invalidateQueries({ queryKey: delayKey }),
      client.invalidateQueries({ queryKey: quantumKey }),
    ]);
  };
  const fail = (fallback: string) => async (error: unknown) => {
    toast.error(errorMessage(error, fallback));
    if (isConflict(error)) await afterEdit();
  };
  const delayEdit = <TInput,>(run: (input: TInput) => Promise<DelayView>, fallback: string, done?: string) =>
    // eslint-disable-next-line react-hooks/rules-of-hooks
    useMutation({
      mutationFn: run,
      onSuccess: async (data) => {
        client.setQueryData(delayKey, data);
        if (done) toast.success(done);
        await afterEdit();
      },
      onError: fail(fallback),
    });
  const quantumEdit = <TInput,>(run: (input: TInput) => Promise<QuantumView>, fallback: string, done?: string) =>
    // eslint-disable-next-line react-hooks/rules-of-hooks
    useMutation({
      mutationFn: run,
      onSuccess: async (data) => {
        client.setQueryData(quantumKey, data);
        if (done) toast.success(done);
        await afterEdit();
      },
      onError: fail(fallback),
    });

  return {
    delay,
    quantum,
    link: delayEdit<{ target_type: string; target_id: string; milestone_ref?: string }>(
      (link) => addHeadLink(claimId, headId, boardVersion, link),
      "The record could not be linked",
      "Linked"
    ),
    unlink: delayEdit<{ linkId: string; refresh?: boolean }>(
      ({ linkId, refresh }) => removeHeadLink(claimId, headId, linkId, boardVersion, refresh),
      "The link could not be changed"
    ),
    settings: delayEdit<Parameters<typeof setDelaySettings>[3]>(
      (settings) => setDelaySettings(claimId, headId, boardVersion, settings),
      "The setting could not be saved"
    ),
    decideEvent: delayEdit<{ eventId: string; risk_owner?: string; criticality?: string; clear?: boolean }>(
      ({ eventId, ...decision }) => decideDelayEvent(claimId, headId, eventId, boardVersion, decision),
      "Your call could not be saved"
    ),
    decideOverlap: delayEdit<{ overlapId: string; treatment?: string; days_to_eot?: number; basis?: string; clear?: boolean }>(
      ({ overlapId, ...decision }) => decideDelayOverlap(claimId, headId, overlapId, boardVersion, decision),
      "The treatment could not be saved",
      "Treatment recorded"
    ),
    recordCostBasis: delayEdit<{
      overlapId: string;
      contractual_basis: string;
      causation: string;
      evidence_document_ids: string[];
    }>(
      ({ overlapId, ...cost }) => recordOverlapCostBasis(claimId, headId, overlapId, boardVersion, cost),
      "The cost basis could not be saved",
      "Cost basis recorded: it needs a review before the days carry cost"
    ),
    reviewCostBasis: delayEdit<{ overlapId: string }>(
      ({ overlapId }) => reviewOverlapCostBasis(claimId, headId, overlapId, boardVersion),
      "The cost basis could not be reviewed",
      "Cost basis reviewed"
    ),
    saveLine: quantumEdit<{ line: QuantumLineInput; lineId?: string }>(
      ({ line, lineId }) => saveQuantumLine(claimId, headId, boardVersion, line, lineId),
      "The line could not be saved"
    ),
    removeLine: quantumEdit<string>(
      (lineId) => removeQuantumLine(claimId, headId, lineId, boardVersion),
      "The line could not be removed"
    ),
  };
}
