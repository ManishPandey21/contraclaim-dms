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
  username?: string;
  email?: string;
  first_name?: string;
  last_name?: string;
  firstName?: string;
  lastName?: string;
  name?: string;
  full_name?: string;
  roles?: string[] | string;
  organization_id?: string | null;
  projects?: string[];
}

const SESSION_PROFILE_CACHE_MS = 5000;

let profileCache:
  | {
      value: SessionProfile;
      cachedAt: number;
    }
  | null = null;
let profileInFlight: Promise<SessionProfile> | null = null;

export function clearSessionProfileCache() {
  profileCache = null;
  profileInFlight = null;
}

export async function loginWithPassword(
  payload: LoginPayload
): Promise<LoginResponse> {
  clearSessionProfileCache();
  const { data } = await publicApi.post<LoginResponse>("/login", payload);
  clearSessionProfileCache();
  return data;
}

export async function getCurrentUserProfile(options?: {
  force?: boolean;
}): Promise<SessionProfile> {
  const force = Boolean(options?.force);
  if (!force && profileCache) {
    const ageMs = Date.now() - profileCache.cachedAt;
    if (ageMs < SESSION_PROFILE_CACHE_MS) {
      return profileCache.value;
    }
  }

  if (!force && profileInFlight) {
    return profileInFlight;
  }

  profileInFlight = (async () => {
    const { data } = await publicApi.get<SessionProfile>("/me", {
      withCredentials: true,
    });

    profileCache = {
      value: data,
      cachedAt: Date.now(),
    };

    return data;
  })();

  try {
    return await profileInFlight;
  } catch (error) {
    profileCache = null;
    throw error;
  } finally {
    profileInFlight = null;
  }
}
