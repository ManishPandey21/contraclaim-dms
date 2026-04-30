import { useCallback, useState } from "react";
import { joinApiUrl } from "@/config/api";
import { LanggraphDraftResponse } from "@/types/langgraph";

export const useLetterGraphRuns = () => {
  const [data, setData] = useState<LanggraphDraftResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRun = useCallback(async (letterId: string) => {
    if (!letterId) {
      setData(null);
      return null;
    }
    setLoading(true);
    setError(null);
    try {
      const token = localStorage.getItem("accessToken") || "";
      const headers: Record<string, string> = token
        ? { Authorization: `Bearer ${token}` }
        : {};
      const res = await fetch(
        joinApiUrl(`/ai-assistant/langgraph/runs/${letterId}`),
        { headers }
      );
      if (res.status === 404) {
        setData(null);
        return null;
      }
      if (!res.ok) {
        throw new Error(`Failed to fetch LangGraph run (${res.status})`);
      }
      const json = (await res.json()) as LanggraphDraftResponse;
      setData(json);
      return json;
    } catch (err: any) {
      const message =
        err?.message ?? "Unable to fetch latest LangGraph run details";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  return { data, loading, error, fetchRun, setData };
};

export type UseLetterGraphRunsHook = ReturnType<typeof useLetterGraphRuns>;
