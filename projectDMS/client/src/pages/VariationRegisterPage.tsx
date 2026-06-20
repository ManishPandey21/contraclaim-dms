import React, { useCallback, useEffect, useState } from "react";
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
import { Download, Edit, GitCompareArrows, Loader2, PlusCircle, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  createVariation,
  deleteVariation,
  exportVariations,
  getVariations,
  getVariationSummary,
  updateVariation,
  VariationDTO,
  VariationPayload,
  VariationSummaryDTO,
} from "@/services/variations-api";
import { enhancedApi } from "@/services/enhanced-api";
import { variationStatusColor, variationStatusLabel, fmtAmount } from "@/lib/contract-controls-helpers";

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
  <Card>
    <CardHeader className="pb-2">
      <CardDescription>{label}</CardDescription>
      <CardTitle className={`text-2xl ${cls || ""}`}>{value}</CardTitle>
    </CardHeader>
  </Card>
);

const VariationRegisterPage: React.FC = () => {
  const [items, setItems] = useState<VariationDTO[]>([]);
  const [summary, setSummary] = useState<VariationSummaryDTO | null>(null);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [projectFilter, setProjectFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<VForm>({ ...EMPTY });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const params: Record<string, string> = {};
      if (projectFilter !== "all") params.project_id = projectFilter;
      if (statusFilter !== "all") params.status = statusFilter;
      if (typeFilter !== "all") params.type = typeFilter;
      setItems(await getVariations(params));
      setSummary(await getVariationSummary(projectFilter !== "all" ? { project_id: projectFilter } : undefined));
    } catch {
      toast.error("Failed to load variations");
    }
  }, [projectFilter, statusFilter, typeFilter]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const ps = await enhancedApi.getProjects();
        if (active) setProjects((ps || []).map((p: any) => ({ id: String(p._id || p.id || ""), name: p.name || "Project" })));
      } catch { /* optional */ }
    })();
    return () => { active = false; };
  }, []);

  const openCreate = () => {
    setEditingId(null);
    setForm({ ...EMPTY, project_id: projectFilter !== "all" ? projectFilter : "" });
    setDialogOpen(true);
  };
  const openEdit = (v: VariationDTO) => {
    setEditingId(v.id);
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
        submitted_amount: form.submitted_amount ? Number(form.submitted_amount) : undefined,
        approved_amount: form.approved_amount ? Number(form.approved_amount) : undefined,
        original_contract_value: form.original_contract_value ? Number(form.original_contract_value) : undefined,
        status: form.status as any,
        remarks: form.remarks || undefined,
      };
      if (editingId) await updateVariation(editingId, payload);
      else await createVariation(payload);
      await load();
      toast.success(editingId ? "Variation updated" : "Variation created");
      setDialogOpen(false);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to save variation");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (v: VariationDTO) => {
    try {
      await deleteVariation(v.id);
      await load();
      toast.success("Variation deleted");
    } catch {
      toast.error("Failed to delete variation");
    }
  };

  const onExport = async (format: "csv" | "xlsx" | "pdf") => {
    try {
      const blob = await exportVariations(format, projectFilter !== "all" ? { project_id: projectFilter } : undefined);
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
          <Button onClick={openCreate}><PlusCircle className="mr-2 h-4 w-4" />Add Variation</Button>
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
          <div className="flex flex-wrap gap-3 pt-3">
            <Select value={projectFilter} onValueChange={setProjectFilter}>
              <SelectTrigger className="w-56"><SelectValue placeholder="Project" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All projects</SelectItem>
                {projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
              </SelectContent>
            </Select>
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
                    <TableCell>{fmtAmount(v.submitted_amount)}</TableCell>
                    <TableCell>{fmtAmount(v.approved_amount)}</TableCell>
                    <TableCell><Badge className={variationStatusColor(v.status)}>{variationStatusLabel(v.status)}</Badge></TableCell>
                    <TableCell>{fmtDate(v.approval_date)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" className="h-8 w-8" title="Edit" onClick={() => openEdit(v)}><Edit className="h-4 w-4" /></Button>
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8" title="Delete"><Trash2 className="h-4 w-4 text-destructive" /></Button>
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
                <Select value={form.project_id} onValueChange={(v) => setForm({ ...form, project_id: v })}>
                  <SelectTrigger><SelectValue placeholder="Select project" /></SelectTrigger>
                  <SelectContent>{projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}</SelectContent>
                </Select>
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
              <div><Label>Submitted</Label><Input type="number" value={form.submitted_amount} onChange={(e) => setForm({ ...form, submitted_amount: e.target.value })} /></div>
              <div><Label>Approved</Label><Input type="number" value={form.approved_amount} onChange={(e) => setForm({ ...form, approved_amount: e.target.value })} /></div>
              <div><Label>Original value</Label><Input type="number" value={form.original_contract_value} onChange={(e) => setForm({ ...form, original_contract_value: e.target.value })} /></div>
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
