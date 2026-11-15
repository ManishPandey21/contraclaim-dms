import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertTriangle, Archive, ArchiveRestore, ArrowLeft, Edit, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import EntityDocumentLinks from "@/components/document-links/EntityDocumentLinks";
import HindranceFormDialog from "@/components/hindrances/HindranceFormDialog";
import HindranceRelationshipLinks from "@/components/hindrances/HindranceRelationshipLinks";
import { useTenant } from "@/contexts/TenantContext";
import useRBAC from "@/hooks/useRBAC";
import { scopeErrorCode } from "@/services/active-scope";
import {
  formatRegisterDate,
  hindranceCategoryLabel,
  hindranceClaimStatusLabel,
  hindranceReference,
  hindranceResponsibilityLabel,
  hindranceStatusClass,
  hindranceStatusLabel,
  hindranceTypeLabel,
} from "@/lib/hindrance-labels";
import { HINDRANCE_DOCUMENT_RELATIONSHIP_ROLES } from "@/services/document-relationships-api";
import {
  archiveHindrance,
  getHindrance,
  getHindranceHistory,
  listHindranceLinks,
  restoreHindrance,
  resyncHindranceTimeline,
  type HindranceDTO,
  type HindranceHistoryEntry,
  type HindranceLinkDTO,
} from "@/services/hindrance-api";

/** `other_project` / `select_project`: the navbar selection does not cover this entry. */
type LoadState = "loading" | "ready" | "not_found" | "forbidden" | "other_project" | "select_project" | "error";

const HISTORY_LABELS: Record<string, string> = {
  "delay_events.created": "Recorded",
  "delay_events.updated": "Edited",
  "delay_events.archived": "Archived",
  "delay_events.restored": "Restored",
  "delay_events.timeline_sync_failed": "Timeline sync failed",
  "delay_event_links.linked": "Relationship linked",
  "delay_event_links.unlinked": "Relationship removed",
};

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

function days(value?: number | null): string | undefined {
  return value === null || value === undefined ? undefined : `${value} day${value === 1 ? "" : "s"}`;
}

export default function HindranceDetailPage() {
  const { id = "" } = useParams<{ id: string }>();
  const { can } = useRBAC();
  const canEdit = can("dms.hindrance.edit");
  const canArchive = can("dms.hindrance.archive");

  const tenant = useTenant();
  const selectedProjectId = tenant.selectedProjectId || "";
  const [item, setItem] = useState<HindranceDTO | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [links, setLinks] = useState<HindranceLinkDTO[]>([]);
  const [linksError, setLinksError] = useState(false);
  const [history, setHistory] = useState<HindranceHistoryEntry[]>([]);
  const [historyError, setHistoryError] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const loadRelations = useCallback(async () => {
    try {
      setLinks(await listHindranceLinks(id));
      setLinksError(false);
    } catch {
      setLinksError(true);
    }
    try {
      setHistory(await getHindranceHistory(id));
      setHistoryError(false);
    } catch {
      setHistory([]);
      setHistoryError(true);
    }
  }, [id]);

  // Re-runs on every project switch: an entry from the previous project must not
  // stay on screen under the new selection. The backend refuses it too (403
  // `context_forbidden`); the project check here is defense in depth.
  const load = useCallback(async () => {
    if (tenant.loading) return;
    setItem(null);
    setState("loading");
    try {
      const loaded = await getHindrance(id);
      if (selectedProjectId && loaded.project_id !== selectedProjectId) {
        setState("other_project");
        return;
      }
      setItem(loaded);
      setState("ready");
      await loadRelations();
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
  }, [id, loadRelations, selectedProjectId, tenant.loading]);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshAfterChange = (saved: HindranceDTO) => {
    setItem(saved);
    void loadRelations();
  };

  const toggleArchive = async () => {
    if (!item || !reason.trim()) return;
    setBusy(true);
    try {
      const saved = item.archived_at ? await restoreHindrance(item.id, reason.trim()) : await archiveHindrance(item.id, reason.trim());
      toast.success(saved.archived_at ? "Entry archived" : "Entry restored");
      setArchiveOpen(false);
      setReason("");
      refreshAfterChange(saved);
    } catch (error) {
      const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : "The change could not be saved");
    } finally {
      setBusy(false);
    }
  };

  const resync = async () => {
    if (!item) return;
    setBusy(true);
    try {
      const saved = await resyncHindranceTimeline(item.id);
      refreshAfterChange(saved);
      if (saved.timeline_sync_status === "synced") toast.success("Contract Timeline updated");
      else toast.error("The Contract Timeline is still unavailable");
    } catch {
      toast.error("Timeline sync could not be started");
    } finally {
      setBusy(false);
    }
  };

  if (state === "loading") {
    return (
      <p className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading register entry…
      </p>
    );
  }
  if (state !== "ready" || !item) {
    const message =
      state === "not_found"
        ? "This register entry does not exist."
        : state === "forbidden"
          ? "You do not have access to this register entry."
          : state === "other_project"
            ? "This register entry is not available in the project selected in the navbar."
            : state === "select_project"
              ? "Select a project in the navbar to open this register entry."
              : "The register entry could not be loaded.";
    return (
      <div role="alert" className="space-y-3 p-6">
        <p className="text-sm">{message}</p>
        <div className="flex gap-2">
          <Button asChild variant="outline" size="sm"><Link to="/hindrances"><ArrowLeft className="mr-2 h-4 w-4" />Back to register</Link></Button>
          {state === "error" && <Button type="button" size="sm" onClick={() => void load()}>Retry</Button>}
        </div>
      </div>
    );
  }

  const archived = Boolean(item.archived_at);
  const byType = (type: HindranceLinkDTO["target_type"]) => links.filter((link) => link.target_type === type);

  return (
    <div className="container mx-auto space-y-6 p-4 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <Link to="/hindrances" className="inline-flex items-center text-sm text-muted-foreground hover:underline">
            <ArrowLeft className="mr-1 h-4 w-4" /> Hindrance &amp; Constraint Register
          </Link>
          <h1 className="text-xl font-bold sm:text-2xl">
            <span className="mr-2 font-mono text-lg text-muted-foreground">{hindranceReference(item)}</span>
            {item.title}
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{hindranceTypeLabel(item.event_type)}</Badge>
            <Badge className={hindranceStatusClass(item.status)}>{hindranceStatusLabel(item.status)}</Badge>
            {archived && <Badge variant="outline">Archived</Badge>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {canEdit && !archived && (
            <Button type="button" variant="outline" onClick={() => setEditOpen(true)}>
              <Edit className="mr-2 h-4 w-4" /> Edit
            </Button>
          )}
          {canArchive && (
            <Button type="button" variant="outline" onClick={() => setArchiveOpen(true)}>
              {archived ? <ArchiveRestore className="mr-2 h-4 w-4" /> : <Archive className="mr-2 h-4 w-4" />}
              {archived ? "Restore" : "Archive"}
            </Button>
          )}
        </div>
      </div>

      {archived && (
        <div role="status" className="rounded-md border bg-muted/40 p-3 text-sm">
          Archived {formatRegisterDate(item.archived_at)}{item.archived_by ? ` by ${item.archived_by}` : ""}
          {item.archive_reason ? ` — ${item.archive_reason}` : ""}. Archived entries are read-only and hidden from the default register view.
        </div>
      )}

      {item.timeline_sync_status === "failed" && (
        <div role="alert" className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm sm:flex-row sm:items-center sm:justify-between">
          <span className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
            This entry is saved, but it is not on the Contract Timeline yet ({item.timeline_sync_error || "projection failed"}).
          </span>
          {canEdit && (
            <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void resync()}>
              <RefreshCw className="mr-2 h-4 w-4" /> Retry timeline sync
            </Button>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader><CardTitle className="text-base">Overview</CardTitle></CardHeader>
            <CardContent>
              <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label="Type" value={hindranceTypeLabel(item.event_type)} />
                <Field label="Category" value={hindranceCategoryLabel(item.category)} />
                <Field label="Status" value={hindranceStatusLabel(item.status)} />
                <Field label="Claim status" value={item.claim_status ? hindranceClaimStatusLabel(item.claim_status) : undefined} />
                <div className="sm:col-span-2"><Field label="Description" value={item.description ? <span className="whitespace-pre-wrap">{item.description}</span> : undefined} /></div>
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-base">Dates &amp; responsibility</CardTitle></CardHeader>
            <CardContent>
              <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <Field label="Start / occurrence" value={formatRegisterDate(item.start_date)} />
                <Field label="End / resolution" value={item.end_date ? formatRegisterDate(item.end_date) : undefined} />
                <Field label="Legacy reference" value={item.hindrance_ref && item.delay_ref ? item.delay_ref : undefined} />
                <Field label="Responsibility" value={hindranceResponsibilityLabel(item.responsibility)} />
                <Field label="Responsible party" value={item.responsible_party} />
                <Field label="Affected party" value={item.affected_party} />
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-base">Impact</CardTitle></CardHeader>
            <CardContent>
              <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div className="sm:col-span-3"><Field label="Impact" value={item.impact_description ? <span className="whitespace-pre-wrap">{item.impact_description}</span> : undefined} /></div>
                <div className="sm:col-span-3"><Field label="Cause" value={item.cause ? <span className="whitespace-pre-wrap">{item.cause}</span> : undefined} /></div>
                <Field label="Location" value={item.location} />
                <Field label="Programme activity (text)" value={item.programme_activity} />
                <Field label="Critical path" value={item.critical_path_impact ? "Affected" : "Not recorded as affected"} />
                <Field label="Float consumed" value={days(item.float_consumed_days)} />
                <Field label="Claimed" value={days(item.claimed_days)} />
                <Field label="Assessed" value={days(item.assessed_days)} />
                <div className="sm:col-span-3"><Field label="Entitlement" value={item.entitlement} /></div>
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-base">Evidence &amp; documents</CardTitle></CardHeader>
            <CardContent>
              <EntityDocumentLinks
                targetType="delay_event"
                targetId={item.id}
                organizationId={item.organization_id}
                projectId={item.project_id}
                roles={HINDRANCE_DOCUMENT_RELATIONSHIP_ROLES}
                defaultRole="site_record"
                canManage={canEdit && !archived}
              />
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          {linksError && (
            <div role="alert" className="flex items-center justify-between gap-2 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
              Relationships could not be loaded.
              <Button type="button" size="sm" variant="outline" onClick={() => void loadRelations()}>Retry</Button>
            </div>
          )}
          <Card>
            <CardHeader><CardTitle className="text-base">Affected activities</CardTitle></CardHeader>
            <CardContent>
              <HindranceRelationshipLinks hindranceId={item.id} projectId={item.project_id} targetType="programme_milestone"
                links={byType("programme_milestone")} canManage={canEdit} archived={archived} onChanged={() => void loadRelations()} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-base">Affected key dates</CardTitle></CardHeader>
            <CardContent>
              <HindranceRelationshipLinks hindranceId={item.id} projectId={item.project_id} targetType="key_date"
                links={byType("key_date")} canManage={canEdit} archived={archived} onChanged={() => void loadRelations()} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-base">EOT submissions</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              <p className="text-xs text-muted-foreground">Linking records support only; it does not create or assess an EOT claim.</p>
              <HindranceRelationshipLinks hindranceId={item.id} projectId={item.project_id} targetType="eot_submission"
                links={byType("eot_submission")} canManage={canEdit} archived={archived} onChanged={() => void loadRelations()} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-base">Timeline &amp; history</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm">
                Contract Timeline:{" "}
                <span className={item.timeline_sync_status === "failed" ? "text-destructive" : ""}>
                  {item.timeline_sync_status === "synced" ? "shown" : item.timeline_sync_status === "failed" ? "not synced" : "not yet synced"}
                </span>
                {item.timeline_sync_status === "synced" && (
                  <> · <Link to="/contracts/timeline" className="text-primary hover:underline">Open timeline</Link></>
                )}
              </p>
              {historyError ? (
                <p role="alert" className="text-sm text-destructive">History could not be loaded.</p>
              ) : history.length === 0 ? (
                <p className="text-sm text-muted-foreground">No history recorded.</p>
              ) : (
                <ol className="space-y-2 border-l pl-4">
                  {history.map((entry, index) => (
                    <li key={entry.id || `${entry.action}-${index}`} className="text-sm">
                      <span className="font-medium">{HISTORY_LABELS[entry.action] || entry.action}</span>
                      <span className="text-xs text-muted-foreground"> · {formatRegisterDate(entry.created_at)}{entry.actor_id ? ` · ${entry.actor_id}` : ""}</span>
                      {entry.reason && <span className="block text-xs text-muted-foreground">{entry.reason}</span>}
                      {entry.changed_fields.length > 0 && entry.action === "delay_events.updated" && (
                        <span className="block text-xs text-muted-foreground">Changed: {entry.changed_fields.join(", ")}</span>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-base">Record</CardTitle></CardHeader>
            <CardContent>
              <dl className="grid grid-cols-1 gap-3">
                <Field label="Recorded" value={`${formatRegisterDate(item.created_at)}${item.created_by ? ` · ${item.created_by}` : ""}`} />
                <Field label="Last updated" value={item.updated_at ? `${formatRegisterDate(item.updated_at)}${item.updated_by ? ` · ${item.updated_by}` : ""}` : undefined} />
              </dl>
            </CardContent>
          </Card>
        </div>
      </div>

      <HindranceFormDialog open={editOpen} onOpenChange={setEditOpen} item={item} onSaved={refreshAfterChange} />

      <AlertDialog open={archiveOpen} onOpenChange={(next) => !busy && setArchiveOpen(next)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{archived ? "Restore this entry?" : "Archive this entry?"}</AlertDialogTitle>
            <AlertDialogDescription>
              {archived
                ? "The entry returns to the active register and can be edited again."
                : "The entry is kept with its full history but becomes read-only and is hidden from the default register view. Nothing is deleted."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div>
            <Label htmlFor="hindrance-archive-reason">Reason *</Label>
            <Textarea id="hindrance-archive-reason" rows={2} value={reason} onChange={(event) => setReason(event.target.value)} />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <Button type="button" disabled={busy || !reason.trim()} onClick={() => void toggleArchive()}>
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {archived ? "Restore" : "Archive"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
