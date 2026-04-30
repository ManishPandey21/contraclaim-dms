import { useCallback, useState } from "react";
import { joinApiUrl } from "@/config/api";
import type {
  StrategyPlanResponse,
  StrategyRole,
} from "@/types/strategyPlan";

export interface StrategyPlanPayload {
  letterId: string;
  role: StrategyRole;
  audience?: string;
  subject: string;
  recipient: string;
  contractorContext?: string;
  engineerContext?: string;
  employerContext?: string;
  summaryPoints?: string[];
  documentIds?: string[];
  requirements?: string;
  points?: string;
  organizationId?: string;
  projectId?: string;
}

export const useLanggraphStrategyPlan = () => {
  const [data, setData] = useState<StrategyPlanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generateStrategyPlan = useCallback(
    async (payload: StrategyPlanPayload): Promise<StrategyPlanResponse> => {
      setLoading(true);
      setError(null);
      try {
        const token = localStorage.getItem("accessToken") || "";
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        };
        if (token) {
          headers.Authorization = `Bearer ${token}`;
        }

        const body = {
          letter_id: payload.letterId,
          role: payload.role,
          audience: payload.audience,
          subject: payload.subject,
          recipient: payload.recipient,
          contractor_context: payload.contractorContext,
          engineer_context: payload.engineerContext,
          employer_context: payload.employerContext,
          summary_points: payload.summaryPoints ?? [],
          document_ids: payload.documentIds ?? [],
          requirements: payload.requirements,
          points: payload.points,
          organization_id: payload.organizationId,
          project_id: payload.projectId,
        };

        const response = await fetch(
          joinApiUrl("/ai-assistant/langgraph/strategy-plan"),
          {
            method: "POST",
            headers,
            body: JSON.stringify(body),
          }
        );

        if (!response.ok) {
          throw new Error(
            `LangGraph strategy plan failed (${response.status})`
          );
        }

        const json = (await response.json()) as StrategyPlanResponse;
        setData(json);
        return json;
      } catch (err: any) {
        const message =
          err?.message ?? "Unable to generate strategy plan";
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

  return { data, loading, error, generateStrategyPlan, reset };
};

export type UseLanggraphStrategyPlanHook = ReturnType<
  typeof useLanggraphStrategyPlan
>;
