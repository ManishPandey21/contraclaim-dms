import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  CalendarDays,
  Check,
  Download,
  FileText,
  GitBranch,
  Link2,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { toast } from "sonner";

import EntityDocumentLinks from "@/components/document-links/EntityDocumentLinks";
import { useTenant } from "@/contexts/TenantContext";
import useRBAC from "@/hooks/useRBAC";
import { scopeErrorCode } from "@/services/active-scope";
import { CHRONOLOGY_EVENT_DOCUMENT_RELATIONSHIP_ROLES } from "@/services/document-relationships-api";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  ChronologyEventDTO,
  MatterChronologyDTO,
  attachChronologyToDraft,
  chronologyExportUrl,
  createChronology,
  extractChronologyEvents,
  getChronologyPleadingContext,
  listChronologies,
  listChronologyEvents,
  rejectChronologyEvent,
  verifyChronologyEvent,
} from "@/services/chronology-api";
import { DocumentItem, listDocuments } from "@/services/documents-api";

const CHRONOLOGY_TYPES = [
  "general_dispute",
  "eot_delay",
  "variation",
  "payment",
  "termination",
  "force_majeure",
  "defect_dlp",
  "bank_guarantee_retention",
  "counterclaim",
  "other",
];

const PERSPECTIVES = ["claimant", "respondent", "neutral"];
const STATUSES = ["all", "ai_suggested", "needs_review", "verified", "edited_verified", "rejected", "duplicate"];

function titleCase(value?: string | null): string {
  return (value || "other").replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function fmtDate(value?: string | null): string {
  if (!value) return "Undated";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString();
}

function docLabel(doc: DocumentItem): string {
  return doc.filename || doc.name || doc._id;
}

function statusClass(status: string): string {
  if (status === "verified" || status === "edited_verified") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (status === "rejected" || status === "duplicate") return "border-red-200 bg-red-50 text-red-700";
  if (status === "needs_review") return "border-orange-200 bg-orange-50 text-orange-700";
  return "border-amber-200 bg-amber-50 text-amber-700";
}

const SummaryTile: React.FC<{ label: string; value: number; tone?: string }> = ({ label, value, tone }) => (
  <div className="rounded-md border bg-white px-4 py-3">
    <div className={cn("text-2xl font-semibold", tone)}>{value}</div>
    <div className="text-xs text-muted-foreground">{label}</div>
  </div>
);

/** Chronology (CL-3B) is held to the navbar selection; say so plainly. */
function scopeMessage(error: unknown, fallback: string): string {
  const code = scopeErrorCode(error);
  if (code === "selection_required") return "Select a project in the navbar to work on chronologies.";
  if (code === "context_forbidden") return "This chronology is not in the project selected in the navbar.";
  return fallback;
}

const EventCard: React.FC<{
  event: ChronologyEventDTO;
  busy: boolean;
  onVerify: (event: ChronologyEventDTO) => void;
  onReject: (event: ChronologyEventDTO) => void;
  /** Documents section open (deep link `?event_id=` opens it). */
  documentsOpen: boolean;
  onToggleDocuments: (event: ChronologyEventDTO) => void;
  canManageDocuments: boolean;
  focused: boolean;
  /** The chronology's scope: an older event may carry none of its own. */
  scope: { organizationId?: string | null; projectId?: string | null };
}> = ({ event, busy, onVerify, onReject, documentsOpen, onToggleDocuments, canManageDocuments, focused, scope }) => (
  <Card
    className={cn("rounded-md", focused && "border-sky-300 ring-1 ring-sky-200")}
    data-testid="chronology-event"
    data-event-id={event.id}
    id={`chronology-event-${event.id}`}
  >
    <CardHeader className="pb-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge variant="outline" className={statusClass(event.verification_status)}>
              {titleCase(event.verification_status)}
            </Badge>
            <Badge variant="secondary">{titleCase(event.event_classification)}</Badge>
            <Badge variant="outline">{titleCase(event.supports_party)}</Badge>
            {event.confidence_score != null && (
              <Badge variant="outline">{Math.round(event.confidence_score * 100)}%</Badge>
            )}
          </div>
          <CardTitle className="text-base">{event.title}</CardTitle>
        </div>
        <div className="flex shrink-0 gap-2">
          {["ai_suggested", "needs_review"].includes(event.verification_status) && (
            <>
              <Button size="sm" variant="outline" className="border-emerald-200 text-emerald-700" disabled={busy} onClick={() => onVerify(event)}>
                {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Check className="mr-1 h-3 w-3" />}
                Verify
              </Button>
              <Button size="sm" variant="outline" className="border-red-200 text-red-700" disabled={busy} onClick={() => onReject(event)}>
                <X className="mr-1 h-3 w-3" />
                Reject
              </Button>
            </>
          )}
          {event.project_event_id && (
            <Badge variant="outline" className="border-sky-200 bg-sky-50 text-sky-700">
              <ShieldCheck className="mr-1 h-3 w-3" />
              Graph synced
            </Badge>
          )}
        </div>
      </div>
    </CardHeader>
    <CardContent className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <CalendarDays className="h-3.5 w-3.5" />
          {fmtDate(event.event_date)} · {titleCase(event.date_type)}
        </span>
        {event.letter_no && <span>{event.letter_no}</span>}
        {event.source_document_name && <span>{event.source_document_name}</span>}
      </div>
      {event.description && <p className="text-sm text-slate-700">{event.description}</p>}
      <div className="flex flex-wrap gap-2">
        {event.contract_clauses.map((clause) => (
          <Badge key={clause} variant="secondary">{clause}</Badge>
        ))}
        {event.issue_tags.map((tag) => (
          <Badge key={tag} variant="outline">{titleCase(tag)}</Badge>
        ))}
      </div>
      <div>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-expanded={documentsOpen}
          aria-controls={documentsOpen ? `chronology-event-documents-${event.id}` : undefined}
          onClick={() => onToggleDocuments(event)}
        >
          <Link2 className="mr-1 h-3.5 w-3.5" />
          {documentsOpen ? "Hide documents" : "Documents"}
        </Button>
        {documentsOpen && (
          <div id={`chronology-event-documents-${event.id}`} className="mt-2 rounded-md border p-3">
            <EntityDocumentLinks
              targetType="chronology_event"
              targetId={event.id}
              organizationId={scope.organizationId || event.organization_id}
              projectId={scope.projectId || event.project_id}
              roles={CHRONOLOGY_EVENT_DOCUMENT_RELATIONSHIP_ROLES}
              defaultRole="correspondence"
              canManage={canManageDocuments}
            />
          </div>
        )}
      </div>
    </CardContent>
  </Card>
);

// Document picker: shows project documents with checkboxes
const DocumentPicker: React.FC<{
  documents: DocumentItem[];
  selected: Set<string>;
  loading: boolean;
  onToggle: (id: string) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
}> = ({ documents, selected, loading, onToggle, onSelectAll, onClearAll }) => {
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Loading project documents…
      </div>
    );
  }
  if (documents.length === 0) {
    return <p className="text-sm text-muted-foreground py-1">No documents found for this project.</p>;
  }
  return (
    <div className="space-y-1">
      <div className="flex gap-3 text-xs mb-2">
        <button type="button" className="text-sky-600 hover:underline" onClick={onSelectAll}>Select all</button>
        <button type="button" className="text-slate-500 hover:underline" onClick={onClearAll}>Clear</button>
        <span className="text-muted-foreground ml-auto">{selected.size} / {documents.length} selected</span>
      </div>
      <div className="max-h-48 overflow-y-auto rounded border divide-y">
        {documents.map((doc) => (
          <label
            key={doc._id}
            className="flex items-center gap-2 px-2 py-1.5 hover:bg-slate-50 cursor-pointer text-sm"
          >
            <Checkbox
              checked={selected.has(doc._id)}
              onCheckedChange={() => onToggle(doc._id)}
            />
            <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{docLabel(doc)}</span>
          </label>
        ))}
      </div>
    </div>
  );
};

const ChronologyBuilderPage: React.FC = () => {
  // CL-3B: the navbar selection is the scope (the server holds every chronology
  // record and write to it). `/chronology/:chronologyId?event_id=` deep-links.
  const tenant = useTenant();
  const scopeOrgId = tenant.selectedOrganizationId || "";
  const scopeProjectId = tenant.selectedProjectId || "";
  const { chronologyId: routeChronologyId = "" } = useParams<{ chronologyId: string }>();
  const [searchParams] = useSearchParams();
  const focusEventId = searchParams.get("event_id") || "";
  const { can } = useRBAC();
  const canEditChronology = can("dms.chronology.edit");
  const [openDocuments, setOpenDocuments] = useState<Set<string>>(
    () => new Set(focusEventId ? [focusEventId] : []),
  );
  const scrolledTo = useRef("");

  // Project documents for the picker
  const [projectDocs, setProjectDocs] = useState<DocumentItem[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);

  // Chronology list + selection
  const [chronologies, setChronologies] = useState<MatterChronologyDTO[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  // A deep-linked chronology that the navbar selection does not contain.
  const [deepLinkRefused, setDeepLinkRefused] = useState(false);
  const [events, setEvents] = useState<ChronologyEventDTO[]>([]);
  const [loading, setLoading] = useState(false);
  const [eventLoading, setEventLoading] = useState(false);
  const [busyEvent, setBusyEvent] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState("all");

  // Extract panel: doc selection
  const [extractDocIds, setExtractDocIds] = useState<Set<string>>(new Set());

  // Attach panel
  const [draftId, setDraftId] = useState("");
  const [contextCount, setContextCount] = useState(0);

  // Creation form
  const [form, setForm] = useState({
    contract_id: "",
    title: "",
    chronology_type: "general_dispute",
    party_perspective: "neutral",
  });
  // Selected doc IDs for creation
  const [createDocIds, setCreateDocIds] = useState<Set<string>>(new Set());

  const selected = useMemo(
    () => chronologies.find((item) => item.id === selectedId) || null,
    [chronologies, selectedId],
  );

  const counts = useMemo(() => {
    const base = { total: events.length, suggested: 0, review: 0, verified: 0, rejected: 0 };
    for (const event of events) {
      if (event.verification_status === "ai_suggested") base.suggested += 1;
      if (event.verification_status === "needs_review") base.review += 1;
      if (event.verification_status === "verified" || event.verification_status === "edited_verified") base.verified += 1;
      if (event.verification_status === "rejected" || event.verification_status === "duplicate") base.rejected += 1;
    }
    return base;
  }, [events]);

  useEffect(() => {
    if (focusEventId) setOpenDocuments((current) => new Set(current).add(focusEventId));
  }, [focusEventId]);

  // Load project documents when project changes
  useEffect(() => {
    setProjectDocs([]);
    setCreateDocIds(new Set());
    setExtractDocIds(new Set());
    if (!scopeProjectId) return;
    setDocsLoading(true);
    listDocuments({ organization_id: scopeOrgId || undefined, project_id: scopeProjectId, limit: 200 })
      .then((res) => setProjectDocs(res.documents ?? []))
      .catch(() => toast.error("Failed to load project documents"))
      .finally(() => setDocsLoading(false));
  }, [scopeOrgId, scopeProjectId]);

  // Re-runs on every project switch; the server bounds the list by the selection.
  const loadChronologies = useCallback(async () => {
    if (tenant.loading) return;
    if (!scopeProjectId) {
      setChronologies([]);
      setSelectedId("");
      return;
    }
    try {
      setLoading(true);
      const rows = await listChronologies();
      setChronologies(rows);
      const refused = Boolean(routeChronologyId) && !rows.some((row) => row.id === routeChronologyId);
      setDeepLinkRefused(refused);
      // A chronology of the previous project never stays selected, and a refused
      // deep link never silently opens another chronology in its place.
      setSelectedId((current) => {
        if (routeChronologyId) return refused ? "" : current && rows.some((row) => row.id === current) ? current : routeChronologyId;
        if (current && rows.some((row) => row.id === current)) return current;
        return rows[0]?.id || "";
      });
    } catch (error) {
      setChronologies([]);
      setSelectedId("");
      toast.error(scopeMessage(error, "Failed to load chronologies"));
    } finally {
      setLoading(false);
    }
  }, [routeChronologyId, scopeProjectId, tenant.loading]);

  const eventSequence = useRef(0);
  const loadEvents = useCallback(async () => {
    const current = ++eventSequence.current;
    if (!selectedId) {
      setEvents([]);
      setContextCount(0);
      return;
    }
    try {
      setEventLoading(true);
      const params = filterStatus === "all" ? undefined : { verification_status: filterStatus };
      const rows = await listChronologyEvents(selectedId, params);
      // A response for a chronology that is no longer selected is dropped.
      if (current !== eventSequence.current) return;
      setEvents(rows);
    } catch (error) {
      if (current !== eventSequence.current) return;
      setEvents([]);
      toast.error(scopeMessage(error, "Failed to load chronology events"));
      return;
    } finally {
      if (current === eventSequence.current) setEventLoading(false);
    }
    // The drafting-source count is separate: its failure keeps the events on screen.
    try {
      const context = await getChronologyPleadingContext(selectedId);
      if (current === eventSequence.current) setContextCount(context.source_ledger.length);
    } catch {
      if (current === eventSequence.current) {
        setContextCount(0);
        toast.error("Drafting sources could not be counted");
      }
    }
  }, [filterStatus, selectedId]);

  useEffect(() => {
    void loadChronologies();
  }, [loadChronologies]);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  // Deep link: bring the linked event into view once, when it has loaded.
  useEffect(() => {
    if (!focusEventId || scrolledTo.current === focusEventId) return;
    if (!events.some((event) => event.id === focusEventId)) return;
    scrolledTo.current = focusEventId;
    document.getElementById(`chronology-event-${focusEventId}`)?.scrollIntoView?.({ block: "center" });
  }, [events, focusEventId]);

  const toggleDocuments = (event: ChronologyEventDTO) =>
    setOpenDocuments((current) => {
      const next = new Set(current);
      if (next.has(event.id)) next.delete(event.id);
      else next.add(event.id);
      return next;
    });

  const submit = async () => {
    if (!scopeProjectId.trim() || !form.title.trim()) {
      toast.error("Project and title are required");
      return;
    }
    try {
      const created = await createChronology({
        organization_id: scopeOrgId || undefined,
        project_id: scopeProjectId,
        contract_id: form.contract_id || undefined,
        title: form.title,
        chronology_type: form.chronology_type,
        party_perspective: form.party_perspective,
        selected_source_ids: Array.from(createDocIds),
        selected_source_types: ["document"],
      });
      toast.success("Chronology created");
      setChronologies((prev) => [created, ...prev]);
      setSelectedId(created.id);
      setCreateDocIds(new Set());
    } catch (error) {
      toast.error(scopeMessage(error, "Unable to create chronology"));
    }
  };

  const extract = async () => {
    if (!selectedId) return;
    try {
      setEventLoading(true);
      const result = await extractChronologyEvents(selectedId, Array.from(extractDocIds));
      toast.success(`Extracted ${result?.events_created ?? 0} chronology events`);
      await loadChronologies();
      await loadEvents();
    } catch {
      toast.error("Chronology extraction failed");
    } finally {
      setEventLoading(false);
    }
  };

  const decide = async (event: ChronologyEventDTO, action: "verify" | "reject") => {
    try {
      setBusyEvent(event.id);
      if (action === "verify") await verifyChronologyEvent(event.chronology_id, event.id);
      else await rejectChronologyEvent(event.chronology_id, event.id);
      await loadEvents();
      toast.success(action === "verify" ? "Event verified" : "Event rejected");
    } catch {
      toast.error("Unable to update event");
    } finally {
      setBusyEvent(null);
    }
  };

  const attach = async () => {
    if (!selectedId || !draftId.trim()) return;
    try {
      const result = await attachChronologyToDraft(draftId.trim(), selectedId);
      toast.success(`Attached ${result?.reference_count ?? 0} chronology references`);
    } catch {
      toast.error("Unable to attach chronology to draft");
    }
  };

  // Helpers for doc picker toggle
  function toggleDoc(set: Set<string>, id: string): Set<string> {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Chronology Builder</h1>
          <p className="mt-1 text-sm text-muted-foreground">Matter chronology, evidence mapping, and arbitration drafting context.</p>
        </div>
        <Button variant="outline" onClick={loadChronologies} disabled={loading}>
          {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
          Refresh
        </Button>
      </div>

      {deepLinkRefused && scopeProjectId && (
        <div role="alert" className="rounded-md border border-destructive/40 bg-white p-4 text-sm text-destructive">
          This chronology is not available in the project selected in the navbar.
        </div>
      )}
      {!tenant.loading && !scopeProjectId && (
        <div role="status" className="rounded-md border border-dashed bg-white p-4 text-sm text-muted-foreground">
          Select a project in the navbar to work on its chronologies.
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <SummaryTile label="Events" value={counts.total} />
        <SummaryTile label="Suggested" value={counts.suggested} tone="text-amber-600" />
        <SummaryTile label="Needs review" value={counts.review} tone="text-orange-600" />
        <SummaryTile label="Verified" value={counts.verified} tone="text-emerald-600" />
        <SummaryTile label="Drafting sources" value={contextCount} tone="text-sky-600" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card className="rounded-md">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Plus className="h-4 w-4" />
                New Chronology
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid gap-2">
                <Label htmlFor="chronology-title">Title</Label>
                <Input
                  id="chronology-title"
                  value={form.title}
                  onChange={(e) => setForm((prev) => ({ ...prev, title: e.target.value }))}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="chronology-contract">Contract ID (optional)</Label>
                <Input
                  id="chronology-contract"
                  value={form.contract_id}
                  onChange={(e) => setForm((prev) => ({ ...prev, contract_id: e.target.value }))}
                />
              </div>
              <div className="grid gap-2">
                <Label>Type</Label>
                <Select value={form.chronology_type} onValueChange={(chronology_type) => setForm((prev) => ({ ...prev, chronology_type }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CHRONOLOGY_TYPES.map((item) => <SelectItem key={item} value={item}>{titleCase(item)}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label>Perspective</Label>
                <Select value={form.party_perspective} onValueChange={(party_perspective) => setForm((prev) => ({ ...prev, party_perspective }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PERSPECTIVES.map((item) => <SelectItem key={item} value={item}>{titleCase(item)}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label>Source documents</Label>
                <DocumentPicker
                  documents={projectDocs}
                  selected={createDocIds}
                  loading={docsLoading}
                  onToggle={(id) => setCreateDocIds((prev) => toggleDoc(prev, id))}
                  onSelectAll={() => setCreateDocIds(new Set(projectDocs.map((d) => d._id)))}
                  onClearAll={() => setCreateDocIds(new Set())}
                />
                {!scopeProjectId && (
                  <p className="text-xs text-muted-foreground">Select a project in the navbar to pick documents.</p>
                )}
              </div>
              <Button className="w-full" onClick={submit} disabled={!scopeProjectId || !form.title.trim()}>
                Create
              </Button>
            </CardContent>
          </Card>

          <Card className="rounded-md">
            <CardHeader>
              <CardTitle className="text-base">Saved Chronologies</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {chronologies.length === 0 ? (
                <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                  {scopeProjectId ? "No chronologies found for this project." : "Select a project in the navbar to load chronologies."}
                </div>
              ) : (
                chronologies.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setSelectedId(item.id)}
                    className={cn(
                      "w-full rounded-md border p-3 text-left text-sm transition-colors hover:bg-slate-50",
                      selectedId === item.id && "border-sky-300 bg-sky-50",
                    )}
                  >
                    <div className="font-medium text-slate-900">{item.title}</div>
                    <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span>{titleCase(item.chronology_type)}</span>
                      <span>{titleCase(item.party_perspective)}</span>
                      <span>{titleCase(item.status)}</span>
                    </div>
                  </button>
                ))
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card className="rounded-md">
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle>{selected?.title || "Select a chronology"}</CardTitle>
                  {selected && (
                    <div className="mt-1 flex flex-wrap gap-2 text-sm text-muted-foreground">
                      <span>{selected.project_id}</span>
                      <span>{titleCase(selected.chronology_type)}</span>
                      <span>{titleCase(selected.party_perspective)}</span>
                    </div>
                  )}
                </div>
                {selected && (
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" asChild>
                      <a href={chronologyExportUrl(selected.id, "docx")} target="_blank" rel="noreferrer">
                        <Download className="mr-1 h-4 w-4" />
                        DOCX
                      </a>
                    </Button>
                    <Button variant="outline" size="sm" asChild>
                      <a href={chronologyExportUrl(selected.id, "xlsx")} target="_blank" rel="noreferrer">
                        <Download className="mr-1 h-4 w-4" />
                        Excel
                      </a>
                    </Button>
                    <Button variant="outline" size="sm" asChild>
                      <a href={chronologyExportUrl(selected.id, "pdf")} target="_blank" rel="noreferrer">
                        <Download className="mr-1 h-4 w-4" />
                        PDF
                      </a>
                    </Button>
                  </div>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Extraction: document picker scoped to same project */}
              <div className="space-y-2">
                <Label className="text-sm font-medium">Documents to extract from</Label>
                <DocumentPicker
                  documents={
                    selected
                      ? projectDocs.filter(
                          (d) => !d.project_id || d.project_id === selected.project_id,
                        )
                      : projectDocs
                  }
                  selected={extractDocIds}
                  loading={docsLoading && !!scopeProjectId}
                  onToggle={(id) => setExtractDocIds((prev) => toggleDoc(prev, id))}
                  onSelectAll={() =>
                    setExtractDocIds(
                      new Set(
                        (selected
                          ? projectDocs.filter(
                              (d) => !d.project_id || d.project_id === selected.project_id,
                            )
                          : projectDocs
                        ).map((d) => d._id),
                      ),
                    )
                  }
                  onClearAll={() => setExtractDocIds(new Set())}
                />
                {!scopeProjectId && !selected && (
                  <p className="text-xs text-muted-foreground">Select a project and chronology first.</p>
                )}
              </div>

              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
                <Select value={filterStatus} onValueChange={setFilterStatus}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {STATUSES.map((item) => <SelectItem key={item} value={item}>{item === "all" ? "All statuses" : titleCase(item)}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Button onClick={extract} disabled={!selectedId || eventLoading}>
                  {eventLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <GitBranch className="mr-2 h-4 w-4" />}
                  Extract
                </Button>
              </div>

              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
                <Input placeholder="Arbitration draft ID" value={draftId} onChange={(e) => setDraftId(e.target.value)} />
                <Button variant="outline" onClick={attach} disabled={!selectedId || !draftId.trim()}>
                  Attach to Draft
                </Button>
              </div>
            </CardContent>
          </Card>

          {eventLoading && events.length === 0 ? (
            <div className="flex min-h-64 items-center justify-center rounded-md border bg-white text-muted-foreground">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              Loading chronology
            </div>
          ) : events.length === 0 ? (
            <div className="flex min-h-64 flex-col items-center justify-center rounded-md border border-dashed bg-white text-center">
              <Search className="mb-3 h-8 w-8 text-muted-foreground" />
              <div className="font-medium text-slate-900">No chronology events found</div>
              <div className="mt-1 max-w-md text-sm text-muted-foreground">
                Select a chronology and run extraction against project documents.
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              {events.map((event) => (
                <EventCard
                  key={event.id}
                  event={event}
                  busy={busyEvent === event.id}
                  onVerify={(row) => decide(row, "verify")}
                  onReject={(row) => decide(row, "reject")}
                  documentsOpen={openDocuments.has(event.id)}
                  onToggleDocuments={toggleDocuments}
                  // An archived chronology is read-only for its evidence (the server refuses too).
                  canManageDocuments={canEditChronology && selected?.status !== "archived"}
                  focused={event.id === focusEventId}
                  scope={{ organizationId: selected?.organization_id, projectId: selected?.project_id }}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ChronologyBuilderPage;
