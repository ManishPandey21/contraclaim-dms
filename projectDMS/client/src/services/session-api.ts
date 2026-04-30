import { publicApi } from "./http";

export interface LoginPayload {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type?: string;
}

export interface SessionProfile {
  id?: string;
  email?: string;
  roles?: string[] | string;
  organization_id?: string | null;
  projects?: string[];
}

export async function loginWithPassword(
  payload: LoginPayload
): Promise<LoginResponse> {
  const { data } = await publicApi.post<LoginResponse>("/login", payload);
  return data;
}

export async function getCurrentUserProfile(
  accessToken?: string
): Promise<SessionProfile> {
  const { data } = await publicApi.get<SessionProfile>("/me", {
    headers: accessToken
      ? {
          Authorization: `Bearer ${accessToken}`,
        }
      : undefined,
  });

  return data;
}
