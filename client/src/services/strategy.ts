import { api } from "./api";
import type {
  StrategyContextResponse,
  StrategyRole,
} from "@/types/strategyPlan";

export async function generateStrategyContexts(
  letterId: string,
  options?: { refresh?: boolean }
): Promise<StrategyContextResponse> {
  const { data } = await api.post<StrategyContextResponse>(
    `/letters/${letterId}/strategy/context`,
    {
      refresh: options?.refresh ?? true,
    }
  );
  return data;
}

export async function saveStrategyRole(
  letterId: string,
  role: StrategyRole,
  recipient?: string
): Promise<void> {
  await api.patch(`/letters/${letterId}/strategy-role`, {
    role,
    recipient,
  });
}
