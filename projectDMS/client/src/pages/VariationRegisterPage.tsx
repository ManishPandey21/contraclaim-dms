import React, { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Download, Edit, GitCompareArrows, Link2, Loader2, PlusCircle, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  createVariation,
  deleteVariation,
  exportVariations,
  getVariation,
  getVariations,
  getVariationSummary,
  updateVariation,
  VariationDTO,
  VariationPayload,
  VariationSummaryDTO,
} from "@/services/variations-api";
import { getContractMasterForProject } from "@/services/contract-master-api";
import { variationStatusColor, variationStatusLabel, fmtAmount } from "@/lib/contract-controls-helpers";
import useRBAC from "@/hooks/useRBAC";
import { useTenant } from "@/contexts/TenantContext";
import { scopeErrorCode } from "@/services/active-scope";
import EntityDocumentLinks from "@/components/document-links/EntityDocumentLinks";
import { VARIATION_DOCUMENT_RELATIONSHIP_ROLES } from "@/services/document-relationships-api";

const STATUS = ["draft", "submitted", "under_review", "recommended", "approved", "rejected", "superseded"];
const TYPES = ["positive", "negative", "neutral"];
const fmtDate = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");

interface VForm {
  project_id: string; variation_number: string; variation_type: string; description: string;
  letter_reference: string; submitted_amount: string; approved_amount: string;
  original_contract_value: string; status: string; remarks: string;
}
const EMPTY: VForm = {
  project_id: "", variation_number: "", variation_type: "positive", description: "",
  letter_reference: "", submitted_amount: "", approved_amount: "", original_contract_value: "",
  status: "draft", remarks: "",
};

const Stat: React.FC<{ label: string; value: string; cls?: string }> = ({ label, value, cls }) => (
  <Card className="flex flex-col">
    <CardHeader>
      <CardDescription className="break-words leading-tight">{label}</CardDescription>
    </CardHeader>
    <CardContent className="mt-auto">
      <CardTitle className={`text-2xl break-words leading-tight ${cls || ""}`}>{value}</CardTitle>
    </CardContent>
  </Card>
);

/** Why a Variation request was refused by the selected-project boundary, for the user. */
function scopeRefusal(error: unknown): string | null {
  const code = scopeErrorCode(error);
  if (code === "selection_required") return "Select a project in the navbar to open or change a variation.";
  if (code === "context_forbidden") return "This variation is not in the project selected in the navbar.";
  return null;
}

const VariationRegisterPage: React.FC = () => {
  const { can } = useRBAC();
  // The navbar selection is the register's scope (CL-3A): the list follows it,
  // a new Variation is filed in it, and records outside it are refused by the
  // server. The selection travels as X-Org-Id / X-Proj-Id on every request.
  const tenant = useTenant();
  const organizationId = tenant.selectedOrganizationId || "";
  const projectId = tenant.selectedProjectId || "";
  const projectName = tenant.selectedProject?.name || "";
  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkId = searchParams.get("variation_id");
  const openedDeepLink = useRef<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // The Variation whose correspondence is open. One linking UI: the shared
  // canonical EntityDocumentLinks component.
  const [linksFor, setLinksFor] = useState<VariationDTO | null>(null);
  const [items, setItems] = useState<VariationDTO[]>([]);
  const [summary, setSummary] = useState<VariationSummaryDTO | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<VForm>({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  // Per-currency split (variation-level). Empty = single-currency (flat amounts).
  const [currencyRows, setCurrencyRows] = useState<
    { currency: string; rate: string; submitted: string; approved: string }[]
  >([]);
  const [vBaseCurrency, setVBaseCurrency] = useState("INR");
  const [vContractCurrencies, setVContractCurrencies] = useState<{ currency: string; conversion_rate: number }[]>([]);

  const sequence = useRef(0);

  // A project switch drops the previous project's rows and open records at
  // once, before the new list arrives: no Project-A Variation is ever shown
  // under Project B. (MainLayout also remounts the page on a switch.)
  useEffect(() => {
    setItems([]);
    setSummary(null);
    setLinksFor(null);
    setDialogOpen(false);
  }, [organizationId, projectId]);

  const load = useCallback(async () => {
    const current = ++sequence.current;
    try {
      const params: Record<string, string> = {};
      if (projectId) params.project_id = projectId;
      if (statusFilter !== "all") params.status = statusFilter;
      if (typeFilter !== "all") params.type = typeFilter;
      const rows = await getVariations(params);
      const totals = await getVariationSummary(projectId ? { project_id: projectId } : undefined);
      if (current !== sequence.current) return;
      setItems(rows);
      setSummary(totals);
    } catch (error) {
      if (current !== sequence.current) return;
      setItems([]);
      setSummary(null);
      toast.error(scopeRefusal(error) || "Failed to load variations");
    } finally {
      if (current === sequence.current) setLoaded(true);
    }
  }, [projectId, statusFilter, typeFilter]);

  useEffect(() => {
    if (tenant.loading) return;
    void load();
  }, [load, tenant.loading]);

  // Deep link from a Document's Linked Records: /variations?variation_id=...
  useEffect(() => {
    if (!deepLinkId || !loaded || openedDeepLink.current === deepLinkId) return;
    openedDeepLink.current = deepLinkId;
    const local = items.find((item) => item.id === deepLinkId);
    if (local) {
      setLinksFor(local);
      return;
    }
    void getVariation(deepLinkId)
      .then(setLinksFor)
      .catch((error) => toast.error(scopeRefusal(error) || "Linked variation could not be opened"));
  }, [deepLinkId, items, loaded]);

  const closeLinks = (open: boolean) => {
    if (open) return;
    setLinksFor(null);
    if (searchParams.get("variation_id")) {
      const next = new URLSearchParams(searchParams);
      next.delete("variation_id");
      setSearchParams(next, { replace: true });
    }
  };

  // Load the contract's currencies for the form's project so a split can be
  // entered in the contract currencies with award-fixed rates.
  useEffect(() => {
    if (!dialogOpen || !form.project_id) { setVContractCurrencies([]); setVBaseCurrency("INR"); return; }
    let active = true;
    (async () => {
      try {
        const cm = await getContractMasterForProject(form.project_id);
        if (!active) return;
        setVBaseCurrency(cm?.currency || "INR");
        setVContractCurrencies((cm?.contract_currencies || []).map((c) => ({ currency: c.currency, conversion_rate: c.conversion_rate })));
      } catch { if (active) { setVContractCurrencies([]); setVBaseCurrency("INR"); } }
    })();
    return () => { active = false; };
  }, [dialogOpen, form.project_id]);

  const rateFor = (cur: string) =>
    cur === vBaseCurrency ? 1 : vContractCurrencies.find((c) => c.currency === cur)?.conversion_rate ?? 1;

  const openCreate = () => {
    if (!projectId) {
      toast.error("Select a project in the navbar to add a variation");
      return;
    }
    setEditingId(null);
    // A new Variation is filed in the selected project; the server refuses any other.
    setForm({ ...EMPTY, project_id: projectId });
    setCurrencyRows([]);
    setDialogOpen(true);
    void getContractMasterForProject(projectId)
      .then((cm) => {
        if (cm?.original_contract_value != null) {
          setForm((f) => (f.original_contract_value ? f : { ...f, original_contract_value: String(cm.original_contract_value) }));
        }
      })
      .catch(() => { /* best-effort */ });
  };
  const openEdit = (v: VariationDTO) => {
    setEditingId(v.id);
    setCurrencyRows(
      (v.currency_amounts || []).map((c) => ({
        currency: c.currency || "",
        rate: c.conversion_rate != null ? String(c.conversion_rate) : "",
        submitted: c.submitted_amount != null ? String(c.submitted_amount) : "",
        approved: c.approved_amount != null ? String(c.approved_amount) : "",
      })),
    );
    setForm({
      project_id: v.project_id || "", variation_number: v.variation_number || "",
      variation_type: v.variation_type, description: v.description || "",
      letter_reference: v.letter_reference || "",
      submitted_amount: v.submitted_amount != null ? String(v.submitted_amount) : "",
      approved_amount: v.approved_amount != null ? String(v.approved_amount) : "",
      original_contract_value: v.original_contract_value != null ? String(v.original_contract_value) : "",
      status: v.status, remarks: v.remarks || "",
    });
    setDialogOpen(true);
  };

  const submit = async () => {
    if (!form.project_id || !form.variation_number.trim()) {
      toast.error("Project and variation number are required");
      return;
    }
    setSaving(true);
    try {
      const payload: VariationPayload = {
        project_id: form.project_id,
        variation_number: form.variation_number.trim(),
        variation_type: form.variation_type as any,
        description: form.description || undefined,
        letter_reference: form.letter_reference || undefined,
        original_contract_value: form.original_contract_value ? Number(form.original_contract_value) : undefined,
        status: form.status as any,
        remarks: form.remarks || undefined,
      };
      const splitRows = currencyRows.filter((r) => r.currency.trim() && (r.submitted || r.approved));
      if (splitRows.length > 0) {
        // Multi-currency: send the per-currency split; base amounts derive server-side.
        payload.currency_amounts = splitRows.map((r) => ({
          currency: r.currency.trim().toUpperCase(),
          conversion_rate: Number(r.rate) || rateFor(r.currency.trim().toUpperCase()),
          submitted_amount: r.submitted ? Number(r.submitted) : undefined,
          approved_amount: r.approved ? Number(r.approved) : undefined,
        }));
      } else {
        payload.currency_amounts = [];
        payload.submitted_amount = form.submitted_amount ? Number(form.submitted_amount) : undefined;
        payload.approved_amount = form.approved_amount ? Number(form.approved_amount) : undefined;
      }
      if (editingId) await updateVariation(editingId, payload);
      else await createVariation(payload);
      await load();
      toast.success(editingId ? "Variation updated" : "Variation created");
      setDialogOpen(false);
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      toast.error(scopeRefusal(e) || (typeof detail === "string" ? detail : "Failed to save variation"));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (v: VariationDTO) => {
    try {
      await deleteVariation(v.id);
      await load();
      toast.success("Variation deleted");
    } catch (error) {
      toast.error(scopeRefusal(error) || "Failed to delete variation");
    }
  };

  const onExport = async (format: "csv" | "xlsx" | "pdf") => {
    try {
      const blob = await exportVariations(format, projectId ? { project_id: projectId } : undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `variation-register.${format}`; a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Export failed");
    }
  };

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GitCompareArrows className="h-6 w-6 text-blue-600" />
          <div>
            <h1 className="text-2xl font-bold">Variation Register</h1>
            <p className="text-sm text-muted-foreground">Contract variations and revised contract value.</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => onExport("csv")}><Download className="mr-2 h-4 w-4" />CSV</Button>
          <Button variant="outline" size="sm" onClick={() => onExport("xlsx")}><Download className="mr-2 h-4 w-4" />Excel</Button>
          <Button variant="outline" size="sm" onClick={() => onExport("pdf")}><Download className="mr-2 h-4 w-4" />PDF</Button>
          <Button onClick={openCreate} disabled={!projectId}
            title={projectId ? undefined : "Select a project in the navbar to add a variation"}>
            <PlusCircle className="mr-2 h-4 w-4" />Add Variation
          </Button>
        </div>
      </div>

      {summary && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          <Stat label="Original Value" value={fmtAmount(summary.original_contract_value)} />
          <Stat label="Approved Variation" value={fmtAmount(summary.cumulative_approved_variation)} cls="text-green-600" />
          <Stat label="Revised Value" value={fmtAmount(summary.revised_contract_value)} cls="text-blue-600" />
          <Stat label="% Variation" value={`${summary.percentage_variation}%`} cls="text-indigo-600" />
          <Stat label="Pending" value={String(summary.pending_variation_count)} cls="text-amber-600" />
          <Stat label="Rejected" value={String(summary.rejected_variation_count)} cls="text-red-600" />
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Variations</CardTitle>
          <CardDescription data-testid="variation-scope">
            {projectId
              ? `Project: ${projectName || projectId}`
              : "All projects you can access. Select a project in the navbar to open, edit or link a variation."}
          </CardDescription>
          <div className="flex flex-wrap gap-3 pt-3">
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-44"><SelectValue placeholder="Status" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {STATUS.map((s) => <SelectItem key={s} value={s}>{variationStatusLabel(s)}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="w-40"><SelectValue placeholder="Type" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All types</SelectItem>
                {TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>No.</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Description</TableHead>
                <TableHead>Submitted</TableHead>
                <TableHead>Approved</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Approval</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.length === 0 ? (
                <TableRow><TableCell colSpan={8} className="py-8 text-center text-muted-foreground">No variations yet.</TableCell></TableRow>
              ) : (
                items.map((v) => (
                  <TableRow key={v.id}>
                    <TableCell className="font-mono text-xs">{v.variation_number || "—"}</TableCell>
                    <TableCell className="text-xs capitalize">{v.variation_type}</TableCell>
                    <TableCell className="max-w-[220px] truncate" title={v.description || ""}>{v.description || "—"}</TableCell>
                    <TableCell>
                      {v.currency_amounts && v.currency_amounts.length > 0
                        ? <span title="multi-currency (base)">{fmtAmount(v.submitted_amount_base)}<span className="ml-1 text-xs text-muted-foreground">≈base</span></span>
                        : fmtAmount(v.submitted_amount)}
                    </TableCell>
                    <TableCell>
                      {v.currency_amounts && v.currency_amounts.length > 0
                        ? <span title="multi-currency (base)">{fmtAmount(v.approved_amount_base)}<span className="ml-1 text-xs text-muted-foreground">≈base</span></span>
                        : fmtAmount(v.approved_amount)}
                    </TableCell>
                    <TableCell><Badge className={variationStatusColor(v.status)}>{variationStatusLabel(v.status)}</Badge></TableCell>
                    <TableCell>{fmtDate(v.approval_date)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" className="h-8 w-8" title="Correspondence"
                          aria-label={`Correspondence for ${v.variation_number || "variation"}`}
                          disabled={!projectId}
                          onClick={() => setLinksFor(v)}><Link2 className="h-4 w-4" /></Button>
                        <Button variant="ghost" size="icon" className="h-8 w-8" title="Edit" disabled={!projectId} onClick={() => openEdit(v)}><Edit className="h-4 w-4" /></Button>
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8" title="Delete" disabled={!projectId}><Trash2 className="h-4 w-4 text-destructive" /></Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Delete variation?</AlertDialogTitle>
                              <AlertDialogDescription>This permanently deletes &quot;{v.variation_number}&quot;.</AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction onClick={() => remove(v)}>Delete</AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={linksFor !== null} onOpenChange={closeLinks}>
        {/* Bounded by the viewport on both axes: the header stays put and the
            links/search body scrolls, so a long result list cannot push the
            dialog (or its close control) off screen. */}
        <DialogContent className="max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] grid-rows-[auto_minmax(0,1fr)] sm:max-w-2xl">
          <DialogHeader className="pr-6">
            <DialogTitle>Correspondence — {linksFor?.variation_number || "Variation"}</DialogTitle>
            <DialogDescription>
              Link existing incoming or outgoing letters to this variation. Unlinking removes the
              relationship only; the letter stays in the register.
            </DialogDescription>
          </DialogHeader>
          {linksFor && (
            <div className="-mx-1 min-h-0 min-w-0 overflow-y-auto px-1">
              <EntityDocumentLinks
                targetType="variation"
                targetId={linksFor.id}
                organizationId={linksFor.organization_id}
                projectId={linksFor.project_id}
                roles={VARIATION_DOCUMENT_RELATIONSHIP_ROLES}
                defaultRole="correspondence"
                canManage={can("dms.variation.edit")}
              />
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingId ? "Edit Variation" : "Add Variation"}</DialogTitle>
            <DialogDescription>Approved amounts roll up into the revised contract value.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Project</Label>
                {/* Fixed to the navbar selection: the server refuses any other project. */}
                <Input value={form.project_id === projectId ? projectName || projectId : form.project_id} readOnly aria-label="Project" />
              </div>
              <div>
                <Label>Variation number</Label>
                <Input value={form.variation_number} onChange={(e) => setForm({ ...form, variation_number: e.target.value })} placeholder="VO-001" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Type</Label>
                <Select value={form.variation_type} onValueChange={(v) => setForm({ ...form, variation_type: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div>
                <Label>Status</Label>
                <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{STATUS.map((s) => <SelectItem key={s} value={s}>{variationStatusLabel(s)}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <Label>Submitted{currencyRows.length > 0 ? ` (${vBaseCurrency})` : ""}</Label>
                <Input type="number" value={form.submitted_amount} disabled={currencyRows.length > 0}
                  onChange={(e) => setForm({ ...form, submitted_amount: e.target.value })} />
              </div>
              <div>
                <Label>Approved{currencyRows.length > 0 ? ` (${vBaseCurrency})` : ""}</Label>
                <Input type="number" value={form.approved_amount} disabled={currencyRows.length > 0}
                  onChange={(e) => setForm({ ...form, approved_amount: e.target.value })} />
              </div>
              <div><Label>Original value</Label><Input type="number" value={form.original_contract_value} onChange={(e) => setForm({ ...form, original_contract_value: e.target.value })} /></div>
            </div>

            <div className="rounded-md border p-3">
              <div className="mb-2 flex items-center justify-between">
                <div>
                  <Label className="text-sm font-medium">Multi-currency split (optional)</Label>
                  <p className="text-xs text-muted-foreground">
                    A variation/BOQ item paid in 2+ currencies. Amounts convert to the
                    base ({vBaseCurrency}) at award-fixed rates. When used, the flat
                    amounts above are ignored.
                  </p>
                </div>
                <Button type="button" variant="outline" size="sm"
                  onClick={() => setCurrencyRows((r) => [...r, { currency: "", rate: "", submitted: "", approved: "" }])}>
                  <PlusCircle className="mr-2 h-4 w-4" /> Add currency
                </Button>
              </div>
              {currencyRows.length > 0 && (
                <div className="space-y-2">
                  <div className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2 text-xs text-muted-foreground">
                    <span>Currency</span><span>Submitted</span><span>Approved</span><span />
                  </div>
                  {currencyRows.map((row, i) => (
                    <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2">
                      <select
                        className="flex h-10 rounded-md border border-input bg-background px-2 text-sm"
                        value={row.currency}
                        onChange={(e) => {
                          const cur = e.target.value;
                          setCurrencyRows((r) => r.map((x, j) => (j === i ? { ...x, currency: cur, rate: String(rateFor(cur)) } : x)));
                        }}
                      >
                        <option value="">—</option>
                        {Array.from(new Set([vBaseCurrency, ...vContractCurrencies.map((c) => c.currency), row.currency].filter(Boolean))).map((c) => (
                          <option key={c} value={c}>{c}{c !== vBaseCurrency ? ` (×${rateFor(c)})` : ""}</option>
                        ))}
                      </select>
                      <Input type="number" placeholder="0" value={row.submitted}
                        onChange={(e) => setCurrencyRows((r) => r.map((x, j) => (j === i ? { ...x, submitted: e.target.value } : x)))} />
                      <Input type="number" placeholder="0" value={row.approved}
                        onChange={(e) => setCurrencyRows((r) => r.map((x, j) => (j === i ? { ...x, approved: e.target.value } : x)))} />
                      <Button type="button" variant="ghost" size="icon" className="h-10 w-10 text-destructive"
                        onClick={() => setCurrencyRows((r) => r.filter((_, j) => j !== i))}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ))}
                  <div className="pt-1 text-sm font-medium">
                    Base ({vBaseCurrency}) — submitted{" "}
                    {fmtAmount(currencyRows.reduce((s, r) => s + (Number(r.submitted) || 0) * (Number(r.rate) || rateFor(r.currency)), 0))}
                    {" · "}approved{" "}
                    {fmtAmount(currencyRows.reduce((s, r) => s + (Number(r.approved) || 0) * (Number(r.rate) || rateFor(r.currency)), 0))}
                  </div>
                </div>
              )}
            </div>
            <div><Label>Letter reference</Label><Input value={form.letter_reference} onChange={(e) => setForm({ ...form, letter_reference: e.target.value })} /></div>
            <div><Label>Description</Label><Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={2} /></div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>Cancel</Button>
            <Button onClick={submit} disabled={saving}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlusCircle className="mr-2 h-4 w-4" />}
              {editingId ? "Save changes" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default VariationRegisterPage;
