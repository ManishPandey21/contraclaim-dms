import { useCallback, useState } from "react";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import type {
  ApproveStageRequest,
  AssignReviewerRequest,
  DraftCommentRequest,
  DraftAuditResponse,
  DraftContextPack,
  DraftGovernanceResponse,
  DraftMode,
  DraftQualityDashboardResponse,
  DraftRunCreateRequest,
  DraftRunAccepted,
  DraftRunCancelRequest,
  DraftRunResumeRequest,
  DraftRunResponse,
  DraftRunStateResponse,
  ExactClauseSearchRequest,
  ExactReferenceSearchRequest,
  LockParagraphsRequest,
  ReviseDraftRequest,
  ReturnForCorrectionRequest,
  SourceLedgerResponse,
  UserDirectionRequest,
} from "@/types/letterDrafting";

async function requestJson<T>(endpoint: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await authenticatedFetch(joinApiUrl(endpoint), {
    ...init,
    headers,
  });
  if (!response.ok) {
    let detail = `Letter drafting request failed (${response.status})`;
    try {
      const payload = await response.json();
      detail = payload?.detail ?? detail;
    } catch {
      // Keep the status-based error.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

const isAcceptedRun = (value: DraftRunResponse | DraftRunAccepted): value is DraftRunAccepted =>
  "poll_url" in value && value.engine === "langgraph_v3";

const pause = (milliseconds: number) => new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

export const useLetterDrafting = () => {
  const [data, setData] = useState<DraftRunResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(
    async (letterId: string, payload: DraftRunCreateRequest) => {
      setLoading(true);
      setError(null);
      try {
        const json = await requestJson<DraftRunResponse | DraftRunAccepted>(
          `/letters/${letterId}/drafting/runs`,
          {
            method: "POST",
            body: JSON.stringify(payload),
            headers: { "Idempotency-Key": crypto.randomUUID() },
          }
        );
        if (!isAcceptedRun(json)) {
          setData(json);
          return json;
        }
        const resolved = await waitForWorkflowRun(letterId, json.run_id);
        setData(resolved);
        return resolved;
      } catch (err: any) {
        const message = err?.message ?? "Unable to run letter drafting workflow";
        setError(message);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    []
  );

  const getWorkflowState = useCallback(
    (letterId: string, runId: string) =>
      requestJson<DraftRunStateResponse>(`/letters/${letterId}/drafting/runs/${runId}/state`),
    []
  );

  const getRun = useCallback(
    (letterId: string, runId: string) =>
      requestJson<DraftRunResponse>(`/letters/${letterId}/drafting/runs/${runId}`),
    []
  );

  const waitForWorkflowRun = useCallback(
    async (letterId: string, runId: string) => {
      for (let attempt = 0; attempt < 30; attempt += 1) {
        const state = await requestJson<DraftRunStateResponse>(
          `/letters/${letterId}/drafting/runs/${runId}/state`
        );
        if (state.next_action !== "poll") {
          return requestJson<DraftRunResponse>(`/letters/${letterId}/drafting/runs/${runId}`);
        }
        await pause(1000);
      }
      return requestJson<DraftRunResponse>(`/letters/${letterId}/drafting/runs/${runId}`);
    },
    []
  );

  const resumeWorkflowRun = useCallback(
    async (letterId: string, runId: string, payload: DraftRunResumeRequest) => {
      setLoading(true);
      try {
        await requestJson<DraftRunStateResponse>(`/letters/${letterId}/drafting/runs/${runId}/resume`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        const resolved = await waitForWorkflowRun(letterId, runId);
        setData(resolved);
        return resolved;
      } finally {
        setLoading(false);
      }
    },
    [waitForWorkflowRun]
  );

  const cancelWorkflowRun = useCallback(
    (letterId: string, runId: string, payload: DraftRunCancelRequest) =>
      requestJson<DraftRunStateResponse>(`/letters/${letterId}/drafting/runs/${runId}/cancel`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    []
  );

  const generateDraft = useCallback(
    (letterId: string, payload: DraftRunCreateRequest) =>
      run(letterId, { ...payload, mode: "draft" }),
    [run]
  );

  // Wrap a drafting request so its resolved run becomes the hook's current
  // `data`. Every run-mutating callback below returns the run and stores it.
  const store = useCallback(
    (pending: Promise<DraftRunResponse>) =>
      pending.then((json) => {
        setData(json);
        return json;
      }),
    []
  );

  // The no-body POST run actions differ only by their trailing URL segment.
  const postAction = useCallback(
    (letterId: string, runId: string, action: string) =>
      store(
        requestJson<DraftRunResponse>(
          `/letters/${letterId}/drafting/runs/${runId}/${action}`,
          { method: "POST", body: "{}" }
        )
      ),
    [store]
  );

  const preparePlan = useCallback(
    (letterId: string, payload: DraftRunCreateRequest) =>
      store(
        requestJson<DraftRunResponse>(`/letters/${letterId}/drafting/prepare-plan`, {
          method: "POST",
          body: JSON.stringify({ ...payload, mode: "strategy" }),
        })
      ),
    [store]
  );

  const acceptPlan = useCallback(
    (letterId: string, runId: string) => postAction(letterId, runId, "accept-plan"),
    [postAction]
  );

  const latestRun = useCallback(
    (letterId: string, mode?: DraftMode) =>
      store(
        requestJson<DraftRunResponse>(
          `/letters/${letterId}/drafting/runs/latest${mode ? `?mode=${mode}` : ""}`
        )
      ),
    [store]
  );

  const getAudit = useCallback(
    (letterId: string, runId: string) =>
      requestJson<DraftAuditResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/audit`
      ),
    []
  );

  const getContextPack = useCallback(
    (letterId: string, runId: string) =>
      requestJson<DraftContextPack>(
        `/letters/${letterId}/drafting/runs/${runId}/context-pack`
      ),
    []
  );

  const getSourceLedger = useCallback(
    (letterId: string, runId: string) =>
      requestJson<SourceLedgerResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/source-ledger`
      ),
    []
  );

  const getGovernance = useCallback(
    (letterId: string, runId: string) =>
      requestJson<DraftGovernanceResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/governance`
      ),
    []
  );

  const getQualityDashboard = useCallback(
    (params?: {
      organizationId?: string;
      projectId?: string;
      windowDays?: number;
    }) => {
      const search = new URLSearchParams();
      if (params?.organizationId) search.set("organization_id", params.organizationId);
      if (params?.projectId) search.set("project_id", params.projectId);
      if (params?.windowDays) search.set("window_days", String(params.windowDays));
      const suffix = search.toString() ? `?${search.toString()}` : "";
      return requestJson<DraftQualityDashboardResponse>(
        `/letter-drafting/metrics/dashboard${suffix}`
      );
    },
    []
  );

  const exactClauseSearch = useCallback(
    (payload: ExactClauseSearchRequest) =>
      requestJson<SourceLedgerResponse>(
        "/letter-drafting/retrieval/exact-clause",
        {
          method: "POST",
          body: JSON.stringify(payload),
        }
      ),
    []
  );

  const exactReferenceSearch = useCallback(
    (payload: ExactReferenceSearchRequest) =>
      requestJson<SourceLedgerResponse>(
        "/letter-drafting/retrieval/exact-reference",
        {
          method: "POST",
          body: JSON.stringify(payload),
        }
      ),
    []
  );

  const reviseRun = useCallback(
    (letterId: string, runId: string, payload: ReviseDraftRequest) =>
      store(
        requestJson<DraftRunResponse>(
          `/letters/${letterId}/drafting/runs/${runId}/revise`,
          {
            method: "POST",
            body: JSON.stringify(payload),
          }
        )
      ),
    [store]
  );

  const validateRun = useCallback(
    (letterId: string, runId: string) => postAction(letterId, runId, "validate"),
    [postAction]
  );

  const critiqueRun = useCallback(
    (letterId: string, runId: string) => postAction(letterId, runId, "critique"),
    [postAction]
  );

  const approveRun = useCallback(
    (letterId: string, runId: string) => postAction(letterId, runId, "approve"),
    [postAction]
  );

  const approveStage = useCallback(
    (letterId: string, runId: string, payload: ApproveStageRequest) =>
      store(
        requestJson<DraftRunResponse>(
          `/letters/${letterId}/drafting/runs/${runId}/approve-stage`,
          { method: "POST", body: JSON.stringify(payload) }
        )
      ),
    [store]
  );

  const provideUserDirection = useCallback(
    (letterId: string, runId: string, payload: UserDirectionRequest) =>
      store(
        requestJson<DraftRunResponse>(
          `/letters/${letterId}/drafting/runs/${runId}/user-direction`,
          { method: "POST", body: JSON.stringify(payload) }
        )
      ),
    [store]
  );

  const lockParagraphs = useCallback(
    (letterId: string, runId: string, payload: LockParagraphsRequest) =>
      store(
        requestJson<DraftRunResponse>(
          `/letters/${letterId}/drafting/runs/${runId}/lock-paragraphs`,
          { method: "POST", body: JSON.stringify(payload) }
        )
      ),
    [store]
  );

  const exportRun = useCallback(
    (letterId: string, runId: string) => postAction(letterId, runId, "export"),
    [postAction]
  );

  const issueRun = useCallback(
    (letterId: string, runId: string, issuedDocumentId?: string) =>
      store(
        requestJson<DraftRunResponse>(
          `/letters/${letterId}/drafting/runs/${runId}/issue`,
          {
            method: "POST",
            body: JSON.stringify({ issued_document_id: issuedDocumentId }),
          }
        )
      ),
    [store]
  );

  const assignReviewer = useCallback(
    (letterId: string, runId: string, payload: AssignReviewerRequest) =>
      requestJson<DraftGovernanceResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/assign-reviewer`,
        {
          method: "POST",
          body: JSON.stringify(payload),
        }
      ),
    []
  );

  const addComment = useCallback(
    (letterId: string, runId: string, payload: DraftCommentRequest) =>
      requestJson<DraftGovernanceResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/comments`,
        {
          method: "POST",
          body: JSON.stringify(payload),
        }
      ),
    []
  );

  const returnForCorrection = useCallback(
    (letterId: string, runId: string, payload: ReturnForCorrectionRequest) =>
      store(
        requestJson<DraftRunResponse>(
          `/letters/${letterId}/drafting/runs/${runId}/return-for-correction`,
          {
            method: "POST",
            body: JSON.stringify(payload),
          }
        )
      ),
    [store]
  );

  const reset = useCallback(() => {
    setData(null);
    setError(null);
  }, []);

  return {
    data,
    loading,
    error,
    run,
    generateDraft,
    preparePlan,
    acceptPlan,
    latestRun,
    getRun,
    getWorkflowState,
    resumeWorkflowRun,
    cancelWorkflowRun,
    getAudit,
    getContextPack,
    getSourceLedger,
    getGovernance,
    getQualityDashboard,
    exactClauseSearch,
    exactReferenceSearch,
    reviseRun,
    validateRun,
    critiqueRun,
    approveRun,
    approveStage,
    provideUserDirection,
    lockParagraphs,
    exportRun,
    issueRun,
    assignReviewer,
    addComment,
    returnForCorrection,
    reset,
  };
};

export type UseLetterDraftingHook = ReturnType<typeof useLetterDrafting>;
