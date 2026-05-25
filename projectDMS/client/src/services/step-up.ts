import { api } from "./api";

const STEP_UP_CACHE_MS = 9 * 60 * 1000;

type CachedStepUpToken = {
  token: string;
  expiresAt: number;
};

const tokenCache = new Map<string, CachedStepUpToken>();

export const STEP_UP_REQUIRED_MESSAGE =
  "Step-up verification is required for this action";

export const isStepUpRequiredError = (error: unknown) => {
  const message =
    error instanceof Error ? error.message : String((error as any)?.message || "");
  const detail = String((error as any)?.response?.data?.detail || "");
  return `${message} ${detail}`.toLowerCase().includes("step-up verification");
};

export const getCachedStepUpToken = (action: string) => {
  const cached = tokenCache.get(action);
  if (!cached || cached.expiresAt <= Date.now()) {
    tokenCache.delete(action);
    return null;
  }
  return cached.token;
};

export const clearCachedStepUpToken = (action?: string) => {
  if (action) tokenCache.delete(action);
  else tokenCache.clear();
};

export const requestStepUpToken = async (
  action: string,
  password: string
): Promise<string> => {
  const { data } = await api.post<{ step_up_token: string }>("/step-up", {
    action,
    password,
  });
  const token = data.step_up_token;
  tokenCache.set(action, {
    token,
    expiresAt: Date.now() + STEP_UP_CACHE_MS,
  });
  return token;
};
