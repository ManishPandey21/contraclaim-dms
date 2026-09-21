import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CalendarDays,
  Check,
  ExternalLink,
  FileText,
  GitBranch,
  History,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  EventLinkDTO,
  ProjectEventDTO,
  TimelineSummaryDTO,
  approveEventLink,
  getContractTimeline,
  getEventLinkHistory,
  rejectEventLink,
  verifyEventLink,
} from "@/services/evidence-graph-api";

const EVENT_TYPES = [
  "letter",
  "instruction",
  "delay",
  "hindrance",
  "constraint",
  "payment",
  "drawing",
  "milestone",
  "meeting",
  "claim",
  "variation",
  "bank_guarantee",
  "key_date",
  "other",
];

const LINK_STATUSES = ["ai_suggested", "user_verified", "approved", "rejected"];
const CLAIM_TYPES = ["eot", "loss_expense", "variation"];
const DELAY_RESPONSIBILITIES = ["employer", "contractor", "concurrent"];
const PAYMENT_STATUSES = ["disputed", "certified", "paid"];
const PAGE_SIZE = 100;

const EMPTY_SUMMARY: TimelineSummaryDTO = {
  total_events: 0,
  graph_links: 0,
  ai_suggested: 0,
  user_verified: 0,
  approved: 0,
  rejected: 0,
  awaiting_review: 0,
};

function titleCase(value?: string | null): string {
  return (value || "other")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function fmtDate(value?: string | null): { day: string; month: string; year: string; full: string } {
  if (!value) return { day: "--", month: "---", year: "----", full: "No date" };
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return { day: "--", month: "---", year: "----", full: value };
  return {
    day: String(date.getDate()).padStart(2, "0"),
    month: date.toLocaleString(undefined, { month: "short" }).toUpperCase(),
    year: String(date.getFullYear()),
    full: date.toLocaleDateString(),
  };
}

function confidenceLabel(value?: number | null): string {
  if (value == null) return "";
  return `${Math.round(value * 100)}%`;
}

function statusClass(status: string): string {
  if (status === "approved" || status === "user_verified") {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (status === "rejected") {
    return "border-red-200 bg-red-50 text-red-700";
  }
  return "border-amber-200 bg-amber-50 text-amber-700";
}

function linkBorder(status: string): string {
  if (status === "approved" || status === "user_verified") return "border-sky-200";
  if (status === "rejected") return "border-red-200";
  return "border-amber-300";
}

function sourcePath(link: EventLinkDTO): string | null {
  const id = encodeURIComponent(link.target_id);
  if (link.target_type === "document" || link.target_type === "letter") return `/documentviewer/${id}`;
  if (link.target_type === "claim") return `/claims/${id}`;
  if (link.target_type === "key_date") return `/key-dates/${id}`;
  if (link.target_type === "variation") return `/variations?reference=${id}`;
  if (link.target_type === "bank_guarantee") return `/bank-guarantees?reference=${id}`;
  if (link.target_type === "payment_event") return `/ipc-bills?reference=${id}`;
  if (link.target_type === "clause") return `/contracts/search?query=${id}`;
  if (link.target_type === "drawing") return `/contracts/search?query=${id}`;
  return null;
}

const SummaryTile: React.FC<{ label: string; value: number; tone?: string }> = ({ label, value, tone }) => (
  <div className="rounded-md border bg-white px-4 py-3">
    <div className={cn("text-2xl font-semibold", tone)}>{value}</div>
    <div className="text-xs text-muted-foreground">{label}</div>
  </div>
);

const FilterSelect: React.FC<{
  value: string;
  placeholder: string;
  allLabel: string;
  options: string[];
  onChange: (value: string) => void;
}> = ({ value, placeholder, allLabel, options, onChange }) => (
  <Select value={value} onValueChange={onChange}>
    <SelectTrigger>
      <SelectValue placeholder={placeholder} />
    </SelectTrigger>
    <SelectContent>
      <SelectItem value="all">{allLabel}</SelectItem>
      {options.map((option) => (
        <SelectItem key={option} value={option}>
          {titleCase(option)}
        </SelectItem>
      ))}
    </SelectContent>
  </Select>
);

const LinkedRecord: React.FC<{
  link: EventLinkDTO;
  onVerify: (link: EventLinkDTO) => void;
  onReject: (link: EventLinkDTO) => void;
  onApprove: (link: EventLinkDTO) => void;
  onHistory: (link: EventLinkDTO) => void;
  busy: boolean;
}> = ({ link, onVerify, onReject, onApprove, onHistory, busy }) => {
  const path = sourcePath(link);
  return (
    <div className={cn("rounded-md border bg-white p-4", linkBorder(link.status))}>
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge variant="outline">{titleCase(link.target_type)}</Badge>
            <Badge variant="secondary">{titleCase(link.relation_type)}</Badge>
            <Badge variant="outline" className={statusClass(link.status)}>
              {titleCase(link.status)}
            </Badge>
            {link.confidence != null && (
              <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">
                AI {confidenceLabel(link.confidence)}
              </Badge>
            )}
          </div>
          <div className="truncate text-sm font-semibold text-slate-900">{link.target_id}</div>
          {link.evidence_text && (
            <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{link.evidence_text}</div>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          <Button size="icon" variant="ghost" disabled={busy} title="Decision history" onClick={() => onHistory(link)}>
            <History className="h-4 w-4" />
          </Button>
          {path && (
            <Button size="icon" variant="ghost" title="Open source" onClick={() => { window.location.href = path; }}>
              <ExternalLink className="h-4 w-4" />
            </Button>
          )}
          {link.status === "ai_suggested" && (
            <>
              <Button size="sm" variant="outline" className="border-emerald-200 text-emerald-700 hover:bg-emerald-50" disabled={busy} onClick={() => onVerify(link)}>
                {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Check className="mr-1 h-3 w-3" />}
                Verify
              </Button>
              <Button size="sm" variant="outline" className="border-red-200 text-red-700 hover:bg-red-50" disabled={busy} onClick={() => onReject(link)}>
                <X className="mr-1 h-3 w-3" />
                Reject
              </Button>
            </>
          )}
          {link.status === "user_verified" && (
            <Button size="sm" variant="outline" className="border-sky-200 text-sky-700 hover:bg-sky-50" disabled={busy} onClick={() => onApprove(link)}>
              <ShieldCheck className="mr-1 h-3 w-3" />
              Approve
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};

const TimelineCard: React.FC<{
  event: ProjectEventDTO;
  onVerify: (link: EventLinkDTO) => void;
  onReject: (link: EventLinkDTO) => void;
  onApprove: (link: EventLinkDTO) => void;
  onHistory: (link: EventLinkDTO) => void;
  busyLink: string | null;
}> = ({ event, onVerify, onReject, onApprove, onHistory, busyLink }) => {
  const d = fmtDate(event.event_date);
  const suggested = event.links.filter((link) => link.status === "ai_suggested").length;
  return (
    <div className="grid grid-cols-[64px_minmax(0,1fr)] gap-4">
      <div className="relative text-center">
        <div className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-slate-200" />
        <div className="relative mx-auto rounded-full border bg-white px-1 py-2 text-xs text-slate-500">
          <div>{d.month}</div>
          <div className="text-xl font-semibold text-slate-900">{d.day}</div>
          <div>{d.year}</div>
        </div>
      </div>
      <Card className="overflow-hidden rounded-md">
        <CardHeader className="border-b bg-white pb-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <div className="rounded-md bg-emerald-50 p-2 text-emerald-700">
                  <FileText className="h-5 w-5" />
                </div>
                <Badge className="bg-emerald-600">{titleCase(event.event_type)}</Badge>
                {event.party && <Badge variant="secondary">{event.party}</Badge>}
                {event.impact_area && <Badge variant="outline">{event.impact_area}</Badge>}
                {suggested > 0 && (
                  <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">
                    {suggested} AI-suggested
                  </Badge>
                )}
              </div>
              <CardTitle className="truncate text-lg">{event.title}</CardTitle>
            </div>
            <Badge variant="outline">{titleCase(event.status)}</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 bg-slate-50 p-4">
          {event.description && <p className="text-sm text-slate-700">{event.description}</p>}
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <CalendarDays className="h-3.5 w-3.5" />
              {d.full}
            </span>
            {event.package && <span>Package: {event.package}</span>}
            {event.source_entity_type && <span>{titleCase(event.source_entity_type)}: {event.source_entity_id}</span>}
          </div>
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-500">
              <GitBranch className="h-4 w-4" />
              Linked Records ({event.links.length})
            </div>
            {event.links.length === 0 ? (
              <div className="rounded-md border border-dashed bg-white p-4 text-sm text-muted-foreground">
                No graph links recorded for this event.
              </div>
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {event.links.map((link) => (
                  <LinkedRecord
                    key={`${link.link_group_id}:${link.revision}`}
                    link={link}
                    onVerify={onVerify}
                    onReject={onReject}
                    onApprove={onApprove}
                    onHistory={onHistory}
                    busy={busyLink === link.link_group_id}
                  />
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

const ContractTimelinePage: React.FC = () => {
  const [events, setEvents] = useState<ProjectEventDTO[]>([]);
  const [summary, setSummary] = useState<TimelineSummaryDTO>(EMPTY_SUMMARY);
  const [loading, setLoading] = useState(false);
  const [busyLink, setBusyLink] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyRows, setHistoryRows] = useState<EventLinkDTO[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [filters, setFilters] = useState({
    event_type: "all",
    link_status: "all",
    claim_type: "all",
    delay_responsibility: "all",
    payment_status: "all",
    date_from: "",
    date_to: "",
    package: "",
    party: "",
    clause: "",
    drawing: "",
    key_date: "",
    location: "",
  });

  const apiParams = useMemo(() => {
    const params: Record<string, string | number> = { limit: PAGE_SIZE };
    for (const [key, value] of Object.entries(filters)) {
      if (value === "all") continue;
      if (value.trim()) params[key] = value.trim();
    }
    return params;
  }, [filters]);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getContractTimeline(apiParams);
      setEvents(data.events);
      setSummary(data.summary);
      setHasMore(data.has_more);
    } catch {
      toast.error("Failed to load contract timeline");
    } finally {
      setLoading(false);
    }
  }, [apiParams]);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (link: EventLinkDTO, action: "verify" | "reject" | "approve") => {
    try {
      setBusyLink(link.link_group_id);
      if (action === "verify") await verifyEventLink(link.link_group_id);
      else if (action === "reject") await rejectEventLink(link.link_group_id);
      else await approveEventLink(link.link_group_id);
      await load();
      toast.success(action === "verify" ? "Link verified" : action === "reject" ? "Link rejected" : "Link approved");
    } catch {
      toast.error("Failed to update graph link");
    } finally {
      setBusyLink(null);
    }
  };

  const showHistory = async (link: EventLinkDTO) => {
    try {
      setHistoryOpen(true);
      setHistoryLoading(true);
      setHistoryRows(await getEventLinkHistory(link.link_group_id));
    } catch {
      toast.error("Failed to load link history");
    } finally {
      setHistoryLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Contract Intelligence Timeline</h1>
          <p className="mt-1 text-sm text-muted-foreground">Chronological project events with verified evidence graph links.</p>
        </div>
        <Button variant="outline" onClick={load} disabled={loading}>
          {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
          Refresh
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
        <SummaryTile label="Timeline events" value={summary.total_events} />
        <SummaryTile label="Graph links" value={summary.graph_links} />
        <SummaryTile label="Awaiting review" value={summary.awaiting_review} tone="text-amber-600" />
        <SummaryTile label="Verified" value={summary.user_verified} tone="text-emerald-600" />
        <SummaryTile label="Approved" value={summary.approved} tone="text-sky-600" />
        <SummaryTile label="Rejected" value={summary.rejected} tone="text-red-600" />
      </div>

      <div className="rounded-md border bg-white p-4">
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
          <FilterSelect value={filters.event_type} placeholder="Event type" allLabel="All event types" options={EVENT_TYPES} onChange={(event_type) => setFilters((prev) => ({ ...prev, event_type }))} />
          <FilterSelect value={filters.link_status} placeholder="Link status" allLabel="All link statuses" options={LINK_STATUSES} onChange={(link_status) => setFilters((prev) => ({ ...prev, link_status }))} />
          <FilterSelect value={filters.claim_type} placeholder="Claim type" allLabel="All claim types" options={CLAIM_TYPES} onChange={(claim_type) => setFilters((prev) => ({ ...prev, claim_type }))} />
          <FilterSelect value={filters.delay_responsibility} placeholder="Delay responsibility" allLabel="Any responsibility" options={DELAY_RESPONSIBILITIES} onChange={(delay_responsibility) => setFilters((prev) => ({ ...prev, delay_responsibility }))} />
          <FilterSelect value={filters.payment_status} placeholder="Payment status" allLabel="Any payment status" options={PAYMENT_STATUSES} onChange={(payment_status) => setFilters((prev) => ({ ...prev, payment_status }))} />
          <Input placeholder="Location" value={filters.location} onChange={(e) => setFilters((prev) => ({ ...prev, location: e.target.value }))} />
          <Input type="date" value={filters.date_from} onChange={(e) => setFilters((prev) => ({ ...prev, date_from: e.target.value }))} />
          <Input type="date" value={filters.date_to} onChange={(e) => setFilters((prev) => ({ ...prev, date_to: e.target.value }))} />
          <Input placeholder="Package" value={filters.package} onChange={(e) => setFilters((prev) => ({ ...prev, package: e.target.value }))} />
          <Input placeholder="Party" value={filters.party} onChange={(e) => setFilters((prev) => ({ ...prev, party: e.target.value }))} />
          <Input placeholder="Clause" value={filters.clause} onChange={(e) => setFilters((prev) => ({ ...prev, clause: e.target.value }))} />
          <Input placeholder="Drawing / key date" value={filters.drawing} onChange={(e) => setFilters((prev) => ({ ...prev, drawing: e.target.value }))} />
          <Input placeholder="Key date" value={filters.key_date} onChange={(e) => setFilters((prev) => ({ ...prev, key_date: e.target.value }))} />
        </div>
      </div>

      {loading && events.length === 0 ? (
        <div className="flex min-h-64 items-center justify-center rounded-md border bg-white text-muted-foreground">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          Loading timeline
        </div>
      ) : events.length === 0 ? (
        <div className="flex min-h-64 flex-col items-center justify-center rounded-md border border-dashed bg-white text-center">
          <Search className="mb-3 h-8 w-8 text-muted-foreground" />
          <div className="font-medium text-slate-900">No timeline events found</div>
          <div className="mt-1 max-w-md text-sm text-muted-foreground">
            Upload and process letters or create project events to populate the evidence graph timeline.
          </div>
        </div>
      ) : (
        <div className="space-y-5">
          {summary.awaiting_review > 0 && (
            <div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              <AlertTriangle className="h-4 w-4" />
              {summary.awaiting_review} AI-suggested link{summary.awaiting_review === 1 ? "" : "s"} awaiting review.
            </div>
          )}
          {events.map((event) => (
            <TimelineCard
              key={event.id}
              event={event}
              onVerify={(link) => decide(link, "verify")}
              onReject={(link) => decide(link, "reject")}
              onApprove={(link) => decide(link, "approve")}
              onHistory={showHistory}
              busyLink={busyLink}
            />
          ))}
          {hasMore && (
            <div className="rounded-md border bg-white px-4 py-3 text-sm text-muted-foreground">
              More timeline events are available. Narrow the filters or increase API pagination in the next implementation slice.
            </div>
          )}
        </div>
      )}

      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Link Decision History</DialogTitle>
          </DialogHeader>
          {historyLoading ? (
            <div className="flex min-h-32 items-center justify-center text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Loading history
            </div>
          ) : (
            <div className="space-y-3">
              {historyRows.map((row) => (
                <div key={`${row.link_group_id}:${row.revision}`} className="rounded-md border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">Revision {row.revision}</Badge>
                    <Badge variant="outline" className={statusClass(row.status)}>{titleCase(row.status)}</Badge>
                    <Badge variant="secondary">{titleCase(row.relation_type)}</Badge>
                    {row.confidence != null && <Badge variant="outline">AI {confidenceLabel(row.confidence)}</Badge>}
                  </div>
                  <div className="mt-2 text-sm font-medium">{titleCase(row.target_type)}: {row.target_id}</div>
                  {row.evidence_text && <div className="mt-1 text-xs text-muted-foreground">{row.evidence_text}</div>}
                  {row.created_at && <div className="mt-2 text-xs text-muted-foreground">{new Date(row.created_at).toLocaleString()}</div>}
                </div>
              ))}
              {historyRows.length === 0 && <div className="text-sm text-muted-foreground">No history found.</div>}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default ContractTimelinePage;
