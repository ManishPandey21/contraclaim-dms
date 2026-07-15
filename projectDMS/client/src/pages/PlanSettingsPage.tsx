import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronDown,
  ChevronRight,
  CircleSlash,
  Clock,
  FolderClosed,
  Package,
  RefreshCw,
  Settings2,
} from "lucide-react";
import { toast } from "sonner";
import useRBAC from "@/hooks/useRBAC";
import { useStepUp } from "@/hooks/useStepUp";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  getPlanSettings,
  PlanSettingsPlan,
  PlanSettingsProject,
  PlanSettingsResponse,
  updatePlanSettingsScope,
} from "@/services/plan-settings-api";

const noServiceValue = "no_service";
const inheritValue = "inherit";

const organizationIdOfProject = (project: PlanSettingsProject) =>
  String(project.organization_id || project.organizationId || "");

const serviceBadge = (enabled: boolean, label: string) => (
  <Badge variant={enabled ? "default" : "outline"}>
    {label}: {enabled ? "Enabled" : "Disabled"}
  </Badge>
);

const statusBadge = (status?: string | null, trial?: boolean) => {
  const normalized = String(trial ? "demo" : status || "none").toLowerCase();
  const label = normalized
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
  if (normalized === "active") {
    return <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400">Active</Badge>;
  }
  if (["expired", "cancelled", "canceled"].includes(normalized)) {
    return <Badge className="bg-red-500/15 text-red-700 dark:text-red-400">{label}</Badge>;
  }
  if (["grace_period", "grace", "demo", "trial", "pilot", "past_due"].includes(normalized)) {
    return <Badge className="bg-amber-500/15 text-amber-700 dark:text-amber-400">{label}</Badge>;
  }
  return <Badge variant="outline">{label}</Badge>;
};

const selectValueForPlan = (planCode?: string | null) =>
  planCode || noServiceValue;

const selectValueForProject = (source?: string, planCode?: string | null) => {
  if (source !== "project") return inheritValue;
  if (planCode === "no_service_override" || !planCode) return noServiceValue;
  return planCode;
};

const PlanSettingsPage = () => {
  const { can, roles, loading: rbacLoading } = useRBAC();
  const [data, setData] = useState<PlanSettingsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const { requestToken, StepUpDialog } = useStepUp();

  const canEditPlans = roles.includes("superadmin");
  const canViewPlans =
    canEditPlans ||
    can("billing.plan.view") ||
    can("subscription.entitlement.manage") ||
    roles.some((role) =>
      ["orgadmin", "orguser", "projectadmin", "projectuser", "contractmgr_org"].includes(role)
    );

  const loadPlanSettings = async () => {
    try {
      setLoading(true);
      const response = await getPlanSettings();
      setData(response);
      setExpanded((current) => {
        if (current.size > 0) return current;
        return new Set(response.organizations.map((org) => String(org.id)));
      });
    } catch (error: any) {
      toast.error("Unable to load plan settings", {
        description:
          error?.response?.data?.detail ||
          error?.message ||
          "Plan settings could not be loaded.",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!rbacLoading && canViewPlans) {
      loadPlanSettings();
    }
  }, [rbacLoading, canViewPlans]);

  const visiblePlans = useMemo(() => {
    return (data?.plans || []).filter(
      (plan) => plan.is_active !== false && plan.code !== "no_service_override"
    );
  }, [data]);

  const projectsByOrg = useMemo(() => {
    const grouped = new Map<string, PlanSettingsProject[]>();
    for (const project of data?.projects || []) {
      const orgId = organizationIdOfProject(project);
      grouped.set(orgId, [...(grouped.get(orgId) || []), project]);
    }
    return grouped;
  }, [data]);

  const planName = (planCode?: string | null) => {
    if (!planCode) return "No service";
    const match = data?.plans.find((plan) => plan.code === planCode);
    return match?.name || planCode;
  };

  const handleOrganizationPlanChange = async (
    organizationId: string,
    value: string
  ) => {
    try {
      setSavingKey(`org:${organizationId}`);
      const stepUpToken = await requestToken(
        "subscription.entitlement.manage",
        "Confirm plan update",
        "Enter your password to change this organization plan."
      );
      const response = await updatePlanSettingsScope({
        organization_id: organizationId,
        project_id: null,
        mode: value === noServiceValue ? "no_service" : "plan",
        plan_code: value === noServiceValue ? null : value,
        status: "active",
      }, {
        stepUpToken,
      });
      setData(response);
      toast.success("Organization plan updated");
    } catch (error: any) {
      toast.error("Unable to update organization plan", {
        description:
          error?.response?.data?.detail ||
          error?.message ||
          "The organization plan was not saved.",
      });
    } finally {
      setSavingKey(null);
    }
  };

  const handleProjectPlanChange = async (
    organizationId: string,
    projectId: string,
    value: string
  ) => {
    try {
      setSavingKey(`project:${projectId}`);
      const stepUpToken = await requestToken(
        "subscription.entitlement.manage",
        "Confirm plan update",
        "Enter your password to change this project plan."
      );
      const response = await updatePlanSettingsScope({
        organization_id: organizationId,
        project_id: projectId,
        mode:
          value === inheritValue
            ? "inherit"
            : value === noServiceValue
              ? "no_service"
              : "plan",
        plan_code:
          value === inheritValue || value === noServiceValue ? null : value,
        status: "active",
      }, {
        stepUpToken,
      });
      setData(response);
      toast.success("Project plan updated");
    } catch (error: any) {
      toast.error("Unable to update project plan", {
        description:
          error?.response?.data?.detail ||
          error?.message ||
          "The project plan was not saved.",
      });
    } finally {
      setSavingKey(null);
    }
  };

  const toggleOrg = (organizationId: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(organizationId)) next.delete(organizationId);
      else next.add(organizationId);
      return next;
    });
  };

  if (rbacLoading) {
    return <div className="container mx-auto p-6">Loading...</div>;
  }

  if (!canViewPlans) {
    return (
      <div className="container mx-auto p-6">
        <Card>
          <CardHeader>
            <CardTitle>Access denied</CardTitle>
          </CardHeader>
          <CardContent>
            You do not have permission to view plan settings.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6 space-y-6">
      {StepUpDialog}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Plan Settings</h1>
          <p className="text-sm text-muted-foreground">
            Assign DMS and drafting services by organization or project.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={loadPlanSettings}
          disabled={loading}
          className="gap-2"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Organizations and Projects</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Plan Selection</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Effective Services</TableHead>
                <TableHead>Billing Period</TableHead>
                <TableHead>Trial / Add-on Status</TableHead>
                <TableHead>Effective Plan & Source</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data?.organizations || []).map((organization) => {
                const orgId = String(organization.id);
                const orgProjects = projectsByOrg.get(orgId) || [];
                const effectiveOrg = data?.effective.organizations[orgId];
                const orgExpanded = expanded.has(orgId);
                return (
                  <React.Fragment key={orgId}>
                    <TableRow>
                      <TableCell>
                        <button
                          type="button"
                          className="flex items-center gap-2 font-medium"
                          onClick={() => toggleOrg(orgId)}
                        >
                          {orgExpanded ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                          {organization.name}
                        </button>
                      </TableCell>
                      <TableCell>
                        {canEditPlans ? (
                          <Select
                            value={selectValueForPlan(effectiveOrg?.plan_code)}
                            onValueChange={(value) =>
                              handleOrganizationPlanChange(orgId, value)
                            }
                            disabled={savingKey === `org:${orgId}`}
                          >
                            <SelectTrigger className="w-[280px]">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value={noServiceValue}>
                                No Service
                              </SelectItem>
                              {visiblePlans.map((plan: PlanSettingsPlan) => (
                                <SelectItem key={plan.code} value={plan.code}>
                                  {plan.name}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        ) : (
                          <span className="text-sm font-medium">
                            {planName(effectiveOrg?.plan_code)}
                          </span>
                        )}
                      </TableCell>
                      <TableCell>
                        {statusBadge(effectiveOrg?.status, effectiveOrg?.trial)}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-2">
                          {serviceBadge(Boolean(effectiveOrg?.dms_enabled), "DMS")}
                          {serviceBadge(
                            Boolean(effectiveOrg?.drafting_enabled),
                            "Drafting"
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <span className="text-xs text-muted-foreground">
                          {effectiveOrg?.billing_period
                            ? effectiveOrg.billing_period.charAt(0).toUpperCase() + effectiveOrg.billing_period.slice(1)
                            : "—"}
                        </span>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {effectiveOrg?.trial && (
                            <Badge variant="outline" className="gap-1 text-amber-600 border-amber-300">
                              <Clock className="h-3 w-3" /> Trial
                            </Badge>
                          )}
                          {(effectiveOrg?.active_add_ons?.length || 0) > 0 && (
                            <Badge variant="outline" className="gap-1">
                              <Package className="h-3 w-3" /> {effectiveOrg?.active_add_ons?.length} add-on{(effectiveOrg?.active_add_ons?.length || 0) > 1 ? "s" : ""}
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {planName(effectiveOrg?.plan_code)}
                      </TableCell>
                    </TableRow>
                    {orgExpanded &&
                      orgProjects.map((project) => {
                        const projectId = String(project.id);
                        const effectiveProject =
                          data?.effective.projects[projectId];
                        const source = effectiveProject?.source || "none";
                        return (
                          <TableRow key={projectId} className="bg-muted/20">
                            <TableCell>
                              <div className="ml-7 flex items-center gap-2">
                                <FolderClosed className="h-4 w-4 text-muted-foreground" />
                                <span>{project.name}</span>
                              </div>
                            </TableCell>
                            <TableCell>
                              {canEditPlans ? (
                                <Select
                                  value={selectValueForProject(
                                    source,
                                    effectiveProject?.plan_code
                                  )}
                                  onValueChange={(value) =>
                                    handleProjectPlanChange(orgId, projectId, value)
                                  }
                                  disabled={savingKey === `project:${projectId}`}
                                >
                                  <SelectTrigger className="w-[280px]">
                                    <SelectValue />
                                  </SelectTrigger>
                                  <SelectContent>
                                    <SelectItem value={inheritValue}>
                                      Inherit Organization Plan
                                    </SelectItem>
                                    <SelectItem value={noServiceValue}>
                                      No Service
                                    </SelectItem>
                                    {visiblePlans.map((plan: PlanSettingsPlan) => (
                                      <SelectItem key={plan.code} value={plan.code}>
                                        {plan.name}
                                      </SelectItem>
                                    ))}
                                  </SelectContent>
                                </Select>
                              ) : (
                                <span className="text-sm font-medium">
                                  {source === "inherited"
                                    ? "Inherit Organization Plan"
                                    : source === "project"
                                      ? planName(effectiveProject?.plan_code)
                                      : "No service"}
                                </span>
                              )}
                            </TableCell>
                            <TableCell>
                              {statusBadge(effectiveProject?.status, effectiveProject?.trial)}
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-wrap gap-2">
                                {serviceBadge(
                                  Boolean(effectiveProject?.dms_enabled),
                                  "DMS"
                                )}
                                {serviceBadge(
                                  Boolean(effectiveProject?.drafting_enabled),
                                  "Drafting"
                                )}
                              </div>
                            </TableCell>
                            <TableCell>
                              <span className="text-xs text-muted-foreground">
                                {effectiveProject?.billing_period
                                  ? effectiveProject.billing_period.charAt(0).toUpperCase() + effectiveProject.billing_period.slice(1)
                                  : "—"}
                              </span>
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-wrap gap-1">
                                {effectiveProject?.trial && (
                                  <Badge variant="outline" className="gap-1 text-amber-600 border-amber-300">
                                    <Clock className="h-3 w-3" /> Trial
                                  </Badge>
                                )}
                                {(effectiveProject?.active_add_ons?.length || 0) > 0 && (
                                  <Badge variant="outline" className="gap-1">
                                    <Package className="h-3 w-3" /> {effectiveProject?.active_add_ons?.length}
                                  </Badge>
                                )}
                              </div>
                            </TableCell>
                            <TableCell className="text-muted-foreground">
                              {source === "inherited"
                                ? `Inherited: ${planName(effectiveProject?.plan_code)}`
                                : source === "project"
                                  ? planName(effectiveProject?.plan_code)
                                  : "No service"}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    {orgExpanded && orgProjects.length === 0 && (
                      <TableRow className="bg-muted/20">
                        <TableCell
                          colSpan={7}
                          className="pl-12 text-muted-foreground"
                        >
                          <div className="flex items-center gap-2">
                            <CircleSlash className="h-4 w-4" />
                            No projects linked to this organization.
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </React.Fragment>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Link to Subscription Management */}
      <div className="flex justify-end">
        <Link to="/subscription-management">
          <Button variant="outline" className="gap-2">
            <Settings2 className="h-4 w-4" />
            Advanced Subscription Management
          </Button>
        </Link>
      </div>
    </div>
  );
};

export default PlanSettingsPage;
