import { useCallback, useEffect, useState } from "react";
import { enhancedApi as api } from "@/services/enhanced-api";
import { getCurrentUserProfile } from "@/services/session-api";

export type IdentityRecord = {
  id?: string | null;
  email?: string | null;
  username?: string | null;
  first_name?: string | null;
  firstName?: string | null;
  last_name?: string | null;
  lastName?: string | null;
  full_name?: string | null;
  name?: string | null;
  profile_photo_url?: string | null;
};

export function resolveSidebarIdentity(
  profile?: IdentityRecord | null,
  session?: IdentityRecord | null,
) {
  const clean = (value: unknown) => String(value || "").trim();
  const fullName = (record?: IdentityRecord | null) => {
    if (!record) return "";
    const first = clean(record.first_name ?? record.firstName);
    const last = clean(record.last_name ?? record.lastName);
    return (
      `${first} ${last}`.trim() ||
      clean(record.full_name) ||
      clean(record.name) ||
      clean(record.username)
    );
  };

  const displayName =
    fullName(profile) ||
    fullName(session) ||
    clean(profile?.email) ||
    clean(session?.email) ||
    clean(session?.id) ||
    "User";
  const parts = displayName.split(/\s+/).filter(Boolean);
  const initials =
    ((parts[0]?.[0] || "U") +
      (parts[1]?.[0] || parts[0]?.[1] || ""))
      .toUpperCase()
      .slice(0, 2);

  return {
    displayName,
    initials,
    profilePhotoUrl: clean(profile?.profile_photo_url),
  };
}

export function useSidebarIdentity() {
  const [identity, setIdentity] = useState(() => resolveSidebarIdentity());

  const load = useCallback(async () => {
    const [profileResult, sessionResult] = await Promise.allSettled([
      api.getProfile(),
      getCurrentUserProfile(),
    ]);
    setIdentity(
      resolveSidebarIdentity(
        profileResult.status === "fulfilled"
          ? (profileResult.value as IdentityRecord)
          : null,
        sessionResult.status === "fulfilled"
          ? (sessionResult.value as IdentityRecord)
          : null,
      ),
    );
  }, []);

  useEffect(() => {
    void load();
    const refresh = () => void load();
    window.addEventListener("profile-updated", refresh);
    return () => window.removeEventListener("profile-updated", refresh);
  }, [load]);

  return identity;
}
