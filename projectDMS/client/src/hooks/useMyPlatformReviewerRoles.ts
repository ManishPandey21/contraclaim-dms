import { useEffect, useState } from "react";

import { fetchMyReviewerRoles } from "@/services/reviewer-assignments-api";

/**
 * The reviewer roles the signed-in person holds at platform scope.
 *
 * Used only to decide whether to show the "Skill reviews" link. A reviewer holds
 * no permission, so nothing else on the client can tell that they are one. The
 * server decides what a reviewer may do; a failed read simply shows no link.
 */
export function useMyPlatformReviewerRoles(): string[] {
  const [roles, setRoles] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    fetchMyReviewerRoles()
      .then((mine) => {
        if (live) setRoles(mine.platform ?? []);
      })
      .catch(() => {
        if (live) setRoles([]);
      });
    return () => {
      live = false;
    };
  }, []);
  return roles;
}
