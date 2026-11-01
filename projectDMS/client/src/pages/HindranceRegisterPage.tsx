import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, Loader2, OctagonAlert, PlusCircle, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import HindranceFormDialog from "@/components/hindrances/HindranceFormDialog";
import { useTenant } from "@/contexts/TenantContext";
import useRBAC from "@/hooks/useRBAC";
import {
  HINDRANCE_CATEGORY_OPTIONS,
  HINDRANCE_RESPONSIBILITY_OPTIONS,
  HINDRANCE_STATUS_OPTIONS,
  HINDRANCE_TYPE_OPTIONS,
  formatRegisterDate,
  fromDateInput,
  hindranceCategoryLabel,
  hindranceReference,
  hindranceResponsibilityLabel,
  hindranceStatusClass,
  hindranceStatusLabel,
  hindranceTypeLabel,
} from "@/lib/hindrance-labels";
import {
  listHindrances,
  type HindranceDTO,
  type HindranceListParams,
  type HindranceSortField,
} from "@/services/hindrance-api";

const PAGE_SIZE = 25;

type Filters = {
  q: string;
  event_type: string;
  status: string;
  responsibility: string;
  category: string;
  start_from: string;
  start_to: string;
  include_archived: boolean;
};

const NO_FILTERS: Filters = {
  q: "",
  event_type: "",
  status: "",
  responsibility: "",
  category: "",
  start_from: "",
  start_to: "",
  include_archived: false,
};

type LoadState = "loading" | "ready" | "error" | "forbidden";

const COLUMNS: Array<{ key: string; label: string; sort?: HindranceSortField; className?: string }> = [
  { key: "ref", label: "Reference", sort: "hindrance_ref" },
  { key: "type", label: "Type", sort: "event_type" },
  { key: "title", label: "Title", sort: "title", className: "min-w-[14rem]" },
  { key: "category", label: "Category", sort: "category" },
  { key: "start", label: "Start date", sort: "start_date" },
  { key: "end", label: "End / resolution", sort: "end_date" },
  { key: "responsibility", label: "Responsibility", sort: "responsibility" },
  { key: "affected", label: "Affected party" },
  { key: "location", label: "Location" },
  { key: "critical", label: "Critical path" },
  { key: "status", label: "Status", sort: "status" },
  { key: "updated", label: "Updated", sort: "updated_at" },
];

const selectClass =
  "h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export default function HindranceRegisterPage() {
  const navigate = useNavigate();
  const { can } = useRBAC();
  const tenant = useTenant();
  const organizationId = tenant.selectedOrganizationId || "";
  const projectId = tenant.selectedProjectId || "";
  const projectName = tenant.selectedProject?.name;
  const canCreate = can("dms.hindrance.create");

  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<HindranceSortField>("start_date");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(0);
  const [items, setItems] = useState<HindranceDTO[]>([]);
  const [total, setTotal] = useState(0);
  const [state, setState] = useState<LoadState>("loading");
  const [dialogOpen, setDialogOpen] = useState(false);
  const sequence = useRef(0);

  // Debounce free-text search so typing does not issue a request per key. Only
  // a changed query resets the page: the timer also fires once after mount,
  // and must not throw the user back to page 1 after they have paged on.
  const appliedQuery = useRef("");
  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (appliedQuery.current === search) return;
      appliedQuery.current = search;
      setFilters((current) => ({ ...current, q: search }));
      setPage(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const params = useMemo<HindranceListParams>(
    () => ({
      organization_id: organizationId || undefined,
      project_id: projectId || undefined,
      q: filters.q.trim() || undefined,
      event_type: (filters.event_type || undefined) as HindranceListParams["event_type"],
      status: (filters.status || undefined) as HindranceListParams["status"],
      responsibility: (filters.responsibility || undefined) as HindranceListParams["responsibility"],
      category: (filters.category || undefined) as HindranceListParams["category"],
      start_from: fromDateInput(filters.start_from) || undefined,
      start_to: filters.start_to ? `${filters.start_to}T23:59:59` : undefined,
      include_archived: filters.include_archived || undefined,
      sort,
      order,
      skip: page * PAGE_SIZE,
      limit: PAGE_SIZE,
    }),
    [filters, order, organizationId, page, projectId, sort],
  );

  // A project switch clears the previous project's rows at once, before the new
  // list arrives: no Project-A row is ever rendered under Project B.
  useEffect(() => {
    setItems([]);
    setTotal(0);
    setPage(0);
  }, [organizationId, projectId]);

  const load = useCallback(async () => {
    const current = ++sequence.current;
    setState("loading");
    try {
      const result = await listHindrances(params);
      if (current !== sequence.current) return;
      setItems(result.items);
      setTotal(result.total);
      setState("ready");
    } catch (error) {
      if (current !== sequence.current) return;
      const status = (error as { response?: { status?: number } })?.response?.status;
      setItems([]);
      setTotal(0);
      setState(status === 403 ? "forbidden" : "error");
    }
  }, [params]);

  useEffect(() => {
    if (tenant.loading) return;
    void load();
  }, [load, tenant.loading]);

  const filtersActive = useMemo(
    () => Object.entries(filters).some(([key, value]) => (key === "include_archived" ? false : Boolean(value))),
    [filters],
  );

  const update = <K extends keyof Filters>(key: K, value: Filters[K]) => {
    setFilters((current) => ({ ...current, [key]: value }));
    setPage(0);
  };

  const toggleSort = (field: HindranceSortField) => {
    if (sort === field) setOrder((current) => (current === "asc" ? "desc" : "asc"));
    else {
      setSort(field);
      setOrder(field === "start_date" || field === "updated_at" ? "desc" : "asc");
    }
    setPage(0);
  };

  const clearFilters = () => {
    appliedQuery.current = "";
    setSearch("");
    setFilters(NO_FILTERS);
    setPage(0);
  };

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const first = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const last = Math.min(total, (page + 1) * PAGE_SIZE);

  return (
    <div className="container mx-auto space-y-6 p-4 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <OctagonAlert className="h-6 w-6 shrink-0 text-amber-600" aria-hidden="true" />
          <div>
            <h1 className="text-xl font-bold sm:text-2xl">Hindrance &amp; Constraint Register</h1>
            <p className="text-sm text-muted-foreground">
              Site hindrances, constraints and delay events{projectName ? ` · ${projectName}` : ""}.
            </p>
          </div>
        </div>
        {canCreate && (
          <div className="flex flex-col items-start gap-1 sm:items-end">
            <Button type="button" onClick={() => setDialogOpen(true)} disabled={!projectId}>
              <PlusCircle className="mr-2 h-4 w-4" /> Record entry
            </Button>
            {!projectId && (
              <span className="text-xs text-muted-foreground">Select a project in the navbar to record an entry.</span>
            )}
          </div>
        )}
      </div>

      {!projectId && !tenant.loading && state !== "forbidden" && (
        <div role="status" className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>No project is selected. Showing every entry you can access across your assigned projects.</span>
        </div>
      )}

      <Card>
        <CardHeader className="flex-col items-stretch justify-start space-y-3">
          <CardTitle className="text-base">Register</CardTitle>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="sm:col-span-2">
              <Label htmlFor="hindrance-search">Search</Label>
              <Input id="hindrance-search" placeholder="Reference, title, location, party…" value={search}
                onChange={(event) => setSearch(event.target.value)} />
            </div>
            <div>
              <Label htmlFor="filter-type">Type</Label>
              <select id="filter-type" className={`${selectClass} w-full`} value={filters.event_type}
                onChange={(event) => update("event_type", event.target.value)}>
                <option value="">All types</option>
                {HINDRANCE_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </div>
            <div>
              <Label htmlFor="filter-status">Status</Label>
              <select id="filter-status" className={`${selectClass} w-full`} value={filters.status}
                onChange={(event) => update("status", event.target.value)}>
                <option value="">All statuses</option>
                {HINDRANCE_STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </div>
            <div>
              <Label htmlFor="filter-responsibility">Responsibility</Label>
              <select id="filter-responsibility" className={`${selectClass} w-full`} value={filters.responsibility}
                onChange={(event) => update("responsibility", event.target.value)}>
                <option value="">Any responsibility</option>
                {HINDRANCE_RESPONSIBILITY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </div>
            <div>
              <Label htmlFor="filter-category">Category</Label>
              <select id="filter-category" className={`${selectClass} w-full`} value={filters.category}
                onChange={(event) => update("category", event.target.value)}>
                <option value="">All categories</option>
                {HINDRANCE_CATEGORY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </div>
            <div>
              <Label htmlFor="filter-from">Started on or after</Label>
              <Input id="filter-from" type="date" value={filters.start_from}
                onChange={(event) => update("start_from", event.target.value)} />
            </div>
            <div>
              <Label htmlFor="filter-to">Started on or before</Label>
              <Input id="filter-to" type="date" value={filters.start_to}
                onChange={(event) => update("start_to", event.target.value)} />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={filters.include_archived}
                onChange={(event) => update("include_archived", event.target.checked)} />
              Include archived
            </label>
            {filtersActive && (
              <Button type="button" variant="ghost" size="sm" onClick={clearFilters}>Clear filters</Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {state === "forbidden" ? (
            <div role="alert" className="py-10 text-center text-sm">
              <p className="font-medium">You do not have access to this register.</p>
              <p className="text-muted-foreground">Ask an administrator for the “View Hindrance &amp; Constraint Register” permission for this project.</p>
            </div>
          ) : state === "error" ? (
            <div role="alert" className="flex flex-col items-center gap-3 py-10 text-sm text-destructive">
              <p>The register could not be loaded.</p>
              <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
                <RefreshCw className="mr-2 h-4 w-4" /> Retry
              </Button>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto" role="region" aria-label="Hindrance register table" tabIndex={0}>
                <Table className="min-w-[72rem]">
                  <TableHeader>
                    <TableRow>
                      {COLUMNS.map((column) => (
                        <TableHead key={column.key} className={column.className}
                          aria-sort={column.sort && sort === column.sort ? (order === "asc" ? "ascending" : "descending") : undefined}>
                          {column.sort ? (
                            <button type="button" className="inline-flex items-center gap-1 font-medium hover:text-foreground"
                              onClick={() => toggleSort(column.sort as HindranceSortField)}>
                              {column.label}
                              {sort === column.sort ? (
                                order === "asc" ? <ArrowUp className="h-3 w-3" aria-hidden="true" /> : <ArrowDown className="h-3 w-3" aria-hidden="true" />
                              ) : (
                                <ArrowUpDown className="h-3 w-3 opacity-40" aria-hidden="true" />
                              )}
                            </button>
                          ) : (
                            column.label
                          )}
                        </TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {state === "loading" ? (
                      <TableRow>
                        <TableCell colSpan={COLUMNS.length} className="py-10 text-center text-muted-foreground">
                          <span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Loading register…</span>
                        </TableCell>
                      </TableRow>
                    ) : items.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={COLUMNS.length} className="py-10 text-center text-muted-foreground">
                          {filtersActive
                            ? "No entries match these filters."
                            : filters.include_archived
                              ? "No entries, archived or active, in this scope."
                              : "No hindrances or constraints recorded yet."}
                        </TableCell>
                      </TableRow>
                    ) : (
                      items.map((item) => (
                        <TableRow key={item.id} className="cursor-pointer" onClick={() => navigate(`/hindrances/${item.id}`)}>
                          <TableCell className="whitespace-nowrap font-mono text-xs">
                            <Link to={`/hindrances/${item.id}`} className="text-primary hover:underline"
                              onClick={(event) => event.stopPropagation()}>
                              {hindranceReference(item)}
                            </Link>
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-xs">{hindranceTypeLabel(item.event_type)}</TableCell>
                          <TableCell className="max-w-[20rem]">
                            <span className="line-clamp-2" title={item.title}>{item.title}</span>
                            {item.archived_at && <Badge variant="outline" className="ml-1 text-[10px]">Archived</Badge>}
                            {item.timeline_sync_status === "failed" && (
                              <Badge variant="outline" className="ml-1 border-destructive text-[10px] text-destructive">Timeline not synced</Badge>
                            )}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-xs">{hindranceCategoryLabel(item.category)}</TableCell>
                          <TableCell className="whitespace-nowrap text-xs">{formatRegisterDate(item.start_date)}</TableCell>
                          <TableCell className="whitespace-nowrap text-xs">{formatRegisterDate(item.end_date)}</TableCell>
                          <TableCell className="whitespace-nowrap text-xs">{hindranceResponsibilityLabel(item.responsibility)}</TableCell>
                          <TableCell className="text-xs">{item.affected_party || "—"}</TableCell>
                          <TableCell className="text-xs">{item.location || "—"}</TableCell>
                          <TableCell className="text-xs">{item.critical_path_impact ? "Yes" : "No"}</TableCell>
                          <TableCell><Badge className={hindranceStatusClass(item.status)}>{hindranceStatusLabel(item.status)}</Badge></TableCell>
                          <TableCell className="whitespace-nowrap text-xs">{formatRegisterDate(item.updated_at || item.created_at)}</TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
              <div className="mt-3 flex flex-col gap-2 text-sm sm:flex-row sm:items-center sm:justify-between">
                <span className="text-muted-foreground" aria-live="polite">
                  {state === "ready" ? `${first}–${last} of ${total}` : " "}
                </span>
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" variant="outline" size="sm" disabled={page === 0 || state === "loading"}
                    onClick={() => setPage((current) => Math.max(0, current - 1))}>Previous</Button>
                  <span className="text-xs text-muted-foreground">Page {page + 1} of {pages}</span>
                  <Button type="button" variant="outline" size="sm" disabled={page + 1 >= pages || state === "loading"}
                    onClick={() => setPage((current) => current + 1)}>Next</Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <HindranceFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        projectId={projectId}
        organizationId={organizationId}
        projectName={projectName}
        onSaved={() => void load()}
      />
    </div>
  );
}
