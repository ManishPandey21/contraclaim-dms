import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import EntityDocumentLinks from "@/components/document-links/EntityDocumentLinks";
import { useTenant } from "@/contexts/TenantContext";
import useRBAC from "@/hooks/useRBAC";
import { formatRegisterDate } from "@/lib/hindrance-labels";
import { scopeErrorCode } from "@/services/active-scope";
import { PROGRAMME_MILESTONE_DOCUMENT_RELATIONSHIP_ROLES } from "@/services/document-relationships-api";
import { getProgrammeMilestone, type ProgrammeMilestoneDetailDTO } from "@/services/programme-milestones-api";

/** `other_project` / `select_project`: the navbar selection does not cover this milestone. */
type LoadState = "loading" | "ready" | "not_found" | "forbidden" | "other_project" | "select_project" | "error";

function titleCase(value?: string | null): string {
  return String(value || "").replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function Field({ label, value }: { label: string; value?: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{value === undefined || value === null || value === "" ? "—" : value}</dd>
    </div>
  );
}

function statusOf(error: unknown): number | undefined {
  return (error as { response?: { status?: number } })?.response?.status;
}

/**
 * A Programme Milestone and its Documents (CL-3B). The deep-link target of the
 * Document Viewer's Linked Records and of a Hindrance's affected activities:
 * `/programme-milestones/:id`. Held to the navbar selection like the server.
 */
export default function ProgrammeMilestoneDetailPage() {
  const { id = "" } = useParams<{ id: string }>();
  const { can } = useRBAC();
  const canManage = can("dms.evidence_graph.manage");
  const tenant = useTenant();
  const selectedProjectId = tenant.selectedProjectId || "";
  const [item, setItem] = useState<ProgrammeMilestoneDetailDTO | null>(null);
  const [state, setState] = useState<LoadState>("loading");

  // Re-runs on every project switch: a milestone of the previous project must not
  // stay on screen under the new selection. The server refuses it too.
  const load = useCallback(async () => {
    if (tenant.loading) return;
    setItem(null);
    setState("loading");
    try {
      const loaded = await getProgrammeMilestone(id);
      if (selectedProjectId && loaded.project_id !== selectedProjectId) {
        setState("other_project");
        return;
      }
      setItem(loaded);
      setState("ready");
    } catch (error) {
      const status = statusOf(error);
      const code = scopeErrorCode(error);
      setState(
        code === "selection_required"
          ? "select_project"
          : code === "context_forbidden"
            ? "other_project"
            : status === 404
              ? "not_found"
              : status === 403
                ? "forbidden"
                : "error",
      );
    }
  }, [id, selectedProjectId, tenant.loading]);

  useEffect(() => {
    void load();
  }, [load]);

  if (state === "loading") {
    return (
      <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading programme milestone…
      </p>
    );
  }
  if (state !== "ready" || !item) {
    const message =
      state === "not_found"
        ? "This programme milestone does not exist."
        : state === "forbidden"
          ? "You do not have access to this programme milestone."
          : state === "other_project"
            ? "This programme milestone is not available in the project selected in the navbar."
            : state === "select_project"
              ? "Select a project in the navbar to open this programme milestone."
              : "The programme milestone could not be loaded.";
    return (
      <div role="alert" className="space-y-3 p-6">
        <p className="text-sm">{message}</p>
        <div className="flex gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to="/hindrances"><ArrowLeft className="mr-2 h-4 w-4" />Hindrance &amp; Constraint Register</Link>
          </Button>
          {state === "error" && <Button type="button" size="sm" onClick={() => void load()}>Retry</Button>}
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto space-y-6 p-4 sm:p-6">
      <div className="space-y-1">
        <p className="text-sm text-muted-foreground">Programme milestone</p>
        <h1 className="text-xl font-bold sm:text-2xl">
          {item.milestone_ref && <span className="mr-2 font-mono text-lg text-muted-foreground">{item.milestone_ref}</span>}
          {item.title}
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{titleCase(item.milestone_type)}</Badge>
          <Badge variant="neutral">{titleCase(item.status)}</Badge>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">Programme</CardTitle></CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Field label="Planned" value={formatRegisterDate(item.planned_date)} />
              <Field label="Forecast" value={item.forecast_date ? formatRegisterDate(item.forecast_date) : undefined} />
              <Field label="Actual" value={item.actual_date ? formatRegisterDate(item.actual_date) : undefined} />
              <Field label="Package" value={item.package} />
              <Field label="Discipline" value={item.discipline} />
              <Field label="Location" value={item.location} />
              <div className="sm:col-span-3">
                <Field label="Description" value={item.description ? <span className="whitespace-pre-wrap">{item.description}</span> : undefined} />
              </div>
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Evidence &amp; documents</CardTitle></CardHeader>
          <CardContent>
            <EntityDocumentLinks
              targetType="programme_milestone"
              targetId={item.id}
              organizationId={item.organization_id}
              projectId={item.project_id}
              roles={PROGRAMME_MILESTONE_DOCUMENT_RELATIONSHIP_ROLES}
              defaultRole="progress_evidence"
              canManage={canManage}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
