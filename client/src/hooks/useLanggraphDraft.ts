import { useCallback, useState } from "react";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import { LanggraphDraftResponse } from "@/types/langgraph";

export interface LanggraphDraftPayload {
  letterId: string;
  subject: string;
  recipient: string;
  context?: string;
  points?: string;
  documentIds?: string[];
  useVectorStore?: boolean;
  analysisOnly?: boolean;
  planOverride?: string;
  includeLetterCodes?: string[];
  excludeLetterCodes?: string[];
}

export const useLanggraphDraft = () => {
  const [data, setData] = useState<LanggraphDraftResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runDraft = useCallback(
    async (payload: LanggraphDraftPayload) => {
      setLoading(true);
      setError(null);
      try {
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        };

        const body = {
          letter_id: payload.letterId,
          subject: payload.subject,
          recipient: payload.recipient,
          context: payload.context,
          points: payload.points,
          document_ids: payload.documentIds ?? [],
          use_vector_store: payload.useVectorStore ?? true,
          analysis_only: payload.analysisOnly ?? false,
          plan_override: payload.planOverride,
          include_letter_codes: payload.includeLetterCodes ?? [],
          exclude_letter_codes: payload.excludeLetterCodes ?? [],
        };

        const endpoint = payload.analysisOnly
          ? "/ai-assistant/langgraph/background"
          : "/ai-assistant/langgraph/draft";

        const response = await authenticatedFetch(joinApiUrl(endpoint), {
          method: "POST",
          headers,
          body: JSON.stringify(body),
        });
        if (!response.ok) {
          throw new Error(`LangGraph draft failed (${response.status})`);
        }
        const json = (await response.json()) as LanggraphDraftResponse;
        setData(json);
        return json;
      } catch (err: any) {
        const message =
          err?.message ?? "Unable to generate draft using LangGraph";
        setError(message);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    []
  );

  const reset = useCallback(() => {
    setData(null);
    setError(null);
  }, []);

  return { data, loading, error, runDraft, reset };
};

export type UseLanggraphDraftHook = ReturnType<typeof useLanggraphDraft>;
