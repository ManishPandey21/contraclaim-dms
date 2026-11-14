import { useEffect, useState } from "react";

import { useOptionalTenant } from "@/contexts/TenantContext";

/**
 * A register page's project filter, bound to the navbar selection (CL-4A).
 *
 * With a project selected in the navbar the register is that project: the
 * filter is pinned to it (the backend refuses any other project anyway, 403
 * `context_forbidden`) and new records default to it. With nothing selected the
 * page keeps its own filter, and the backend bounds the list by membership.
 * `MainLayout` remounts the page on a navbar switch, so no Project-A row
 * survives into Project B.
 */
export function useRegisterProjectScope(initialFilter = "all") {
  const tenant = useOptionalTenant();
  const selectedProjectId = tenant?.selectedProjectId || "";
  const [chosen, setProjectFilter] = useState(initialFilter);
  return {
    /** The filter to send: the navbar project when one is selected. */
    projectFilter: selectedProjectId || chosen,
    setProjectFilter,
    /** True while the navbar pins the project; the page's own picker is disabled. */
    projectLocked: Boolean(selectedProjectId),
    selectedProjectId,
    selectedOrganizationId: tenant?.selectedOrganizationId || "",
    /** Wait for the selection before the first load, so it carries the right project. */
    tenantLoading: Boolean(tenant?.loading),
  };
}

/**
 * Pin a page's own organisation/project picker state to the navbar selection
 * (CL-4A). Pages that predate the navbar (the Contracts pages) seed their picker
 * from storage; while the navbar selects a project, a divergent value would only
 * earn a 403 `context_forbidden`, so it snaps back to the selection.
 */
export function usePinnedPageScope(
  organizationId: string,
  setOrganizationId: (value: string) => void,
  projectId: string,
  setProjectId: (value: string) => void,
): boolean {
  const tenant = useOptionalTenant();
  const selectedProjectId = tenant?.selectedProjectId || "";
  const selectedOrganizationId = tenant?.selectedOrganizationId || "";
  useEffect(() => {
    if (!selectedProjectId) return;
    if (organizationId !== selectedOrganizationId) setOrganizationId(selectedOrganizationId);
    if (projectId !== selectedProjectId) setProjectId(selectedProjectId);
  }, [selectedOrganizationId, selectedProjectId, organizationId, projectId, setOrganizationId, setProjectId]);
  return Boolean(selectedProjectId);
}
