import { useCallback, useState } from "react";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import type {
  AssignReviewerRequest,
  DraftCommentRequest,
  DraftAuditResponse,
  DraftContextPack,
  DraftGovernanceResponse,
  DraftMode,
  DraftQualityDashboardResponse,
  DraftRunCreateRequest,
  DraftRunResponse,
  ExactClauseSearchRequest,
  ExactReferenceSearchRequest,
  ReviseDraftRequest,
  ReturnForCorrectionRequest,
  SourceLedgerResponse,
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

export const useLetterDrafting = () => {
  const [data, setData] = useState<DraftRunResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(
    async (letterId: string, payload: DraftRunCreateRequest) => {
      setLoading(true);
      setError(null);
      try {
        const json = await requestJson<DraftRunResponse>(
          `/letters/${letterId}/drafting/runs`,
          {
            method: "POST",
            body: JSON.stringify(payload),
          }
        );
        setData(json);
        return json;
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

  const generateDraft = useCallback(
    (letterId: string, payload: DraftRunCreateRequest) =>
      run(letterId, { ...payload, mode: "draft" }),
    [run]
  );

  const preparePlan = useCallback(
    (letterId: string, payload: DraftRunCreateRequest) =>
      requestJson<DraftRunResponse>(`/letters/${letterId}/drafting/prepare-plan`, {
        method: "POST",
        body: JSON.stringify({ ...payload, mode: "strategy" }),
      }).then((json) => {
        setData(json);
        return json;
      }),
    []
  );

  const acceptPlan = useCallback(
    (letterId: string, runId: string) =>
      requestJson<DraftRunResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/accept-plan`,
        { method: "POST", body: "{}" }
      ).then((json) => {
        setData(json);
        return json;
      }),
    []
  );

  const latestRun = useCallback(
    (letterId: string, mode?: DraftMode) =>
      requestJson<DraftRunResponse>(
        `/letters/${letterId}/drafting/runs/latest${mode ? `?mode=${mode}` : ""}`
      ).then((json) => {
        setData(json);
        return json;
      }),
    []
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
      requestJson<DraftRunResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/revise`,
        {
          method: "POST",
          body: JSON.stringify(payload),
        }
      ).then((json) => {
        setData(json);
        return json;
      }),
    []
  );

  const validateRun = useCallback(
    (letterId: string, runId: string) =>
      requestJson<DraftRunResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/validate`,
        { method: "POST", body: "{}" }
      ).then((json) => {
        setData(json);
        return json;
      }),
    []
  );

  const critiqueRun = useCallback(
    (letterId: string, runId: string) =>
      requestJson<DraftRunResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/critique`,
        { method: "POST", body: "{}" }
      ).then((json) => {
        setData(json);
        return json;
      }),
    []
  );

  const approveRun = useCallback(
    (letterId: string, runId: string) =>
      requestJson<DraftRunResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/approve`,
        { method: "POST", body: "{}" }
      ).then((json) => {
        setData(json);
        return json;
      }),
    []
  );

  const exportRun = useCallback(
    (letterId: string, runId: string) =>
      requestJson<DraftRunResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/export`,
        { method: "POST", body: "{}" }
      ).then((json) => {
        setData(json);
        return json;
      }),
    []
  );

  const issueRun = useCallback(
    (letterId: string, runId: string, issuedDocumentId?: string) =>
      requestJson<DraftRunResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/issue`,
        {
          method: "POST",
          body: JSON.stringify({ issued_document_id: issuedDocumentId }),
        }
      ).then((json) => {
        setData(json);
        return json;
      }),
    []
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
      requestJson<DraftRunResponse>(
        `/letters/${letterId}/drafting/runs/${runId}/return-for-correction`,
        {
          method: "POST",
          body: JSON.stringify(payload),
        }
      ).then((json) => {
        setData(json);
        return json;
      }),
    []
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
    exportRun,
    issueRun,
    assignReviewer,
    addComment,
    returnForCorrection,
    reset,
  };
};

export type UseLetterDraftingHook = ReturnType<typeof useLetterDrafting>;
