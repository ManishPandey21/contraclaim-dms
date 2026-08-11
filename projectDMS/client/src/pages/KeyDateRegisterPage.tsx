import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
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
import { CalendarClock, Download, Edit, Eye, Loader2, PlusCircle, RefreshCw, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import CsvImportDialog from "@/components/registers/CsvImportDialog";
import KeyDateRevisionWorkflow from "@/components/key-dates/KeyDateRevisionWorkflow";
import {
  createMilestone,
  deleteMilestone,
  downloadKeyDatesImportTemplate,
  exportKeyDates,
  getKeyDateDashboard,
  getMilestones,
  importKeyDatesCsv,
  KeyDateDashboardDTO,
  MilestoneDTO,
  MilestonePayload,
  previewKeyDatesCsv,
  recalculateKeyDates,
  updateMilestone,
} from "@/services/key-dates-api";
import { enhancedApi } from "@/services/enhanced-api";
import { statusColor, statusLabel, alertText, achievementText, maxRevisionCount, revisionAt } from "@/lib/key-date-helpers";

const STATUS_OPTIONS = [
  "not_started", "upcoming", "due_soon", "due_today", "overdue",
  "achieved", "eot_submitted", "eot_under_review", "extension_approved", "extension_rejected",
];

const fmtDate = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");

interface MForm {
  title: string;
  project_id: string;
  contractual_week_number: string;
  project_start_date: string;
  milestone_ref: string;
  description: string;
  responsible_party_id: string;
  remarks: string;
}

const EMPTY: MForm = {
  title: "", project_id: "", contractual_week_number: "", project_start_date: "",
  milestone_ref: "", description: "", responsible_party_id: "", remarks: "",
};

const Stat: React.FC<{ label: string; value: number; cls?: string }> = ({ label, value, cls }) => (
  <Card className="flex flex-col">
    <CardHeader>
      <CardDescription className="break-words leading-tight">{label}</CardDescription>
    </CardHeader>
    <CardContent className="mt-auto">
      <CardTitle className={`text-3xl break-words leading-tight ${cls || ""}`}>{value}</CardTitle>
    </CardContent>
  </Card>
);

const KeyDateRegisterPage: React.FC = () => {
  const [items, setItems] = useState<MilestoneDTO[]>([]);
  const [dash, setDash] = useState<KeyDateDashboardDTO | null>(null);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [projectFilter, setProjectFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [csvOpen, setCsvOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<MForm>({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  const [baselineFrozen, setBaselineFrozen] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const params: Record<string, string> = {};
      if (projectFilter !== "all") params.project_id = projectFilter;
      if (statusFilter !== "all") params.status = statusFilter;
      setItems(await getMilestones(params));
      setDash(await getKeyDateDashboard(projectFilter !== "all" ? { project_id: projectFilter } : undefined));
    } catch {
      toast.error("Failed to load key dates");
    }
  }, [projectFilter, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const ps = await enhancedApi.getProjects();
        if (active) setProjects((ps || []).map((p: any) => ({ id: String(p._id || p.id || ""), name: p.name || "Project" })));
      } catch {
        /* projects optional */
      }
    })();
    return () => { active = false; };
  }, []);

  const openCreate = () => {
    if (baselineFrozen) {
      toast.error("Original Key Dates are frozen. Create an EOT submission instead.");
      return;
    }
    setEditingId(null);
    setForm({ ...EMPTY, project_id: projectFilter !== "all" ? projectFilter : "" });
    setDialogOpen(true);
  };

  const openEdit = (m: MilestoneDTO) => {
    if (baselineFrozen) {
      toast.error("Frozen Original Key Dates are read-only.");
      return;
    }
    setEditingId(m.id);
    setForm({
      title: m.title,
      project_id: m.project_id || "",
      contractual_week_number: String(m.contractual_week_number ?? ""),
      project_start_date: "",
      milestone_ref: m.milestone_ref || "",
      description: m.description || "",
      responsible_party_id: m.responsible_party_id || "",
      remarks: m.remarks || "",
    });
    setDialogOpen(true);
  };

  const submit = async () => {
    if (!form.title.trim() || !form.project_id || !form.contractual_week_number) {
      toast.error("Title, project and contractual week number are required");
      return;
    }
    setSaving(true);
    try {
      const payload: MilestonePayload = {
        title: form.title.trim(),
        project_id: form.project_id,
        contractual_week_number: Number(form.contractual_week_number),
        project_start_date: form.project_start_date ? new Date(form.project_start_date).toISOString() : undefined,
        milestone_ref: form.milestone_ref || undefined,
        description: form.description || undefined,
        responsible_party_id: form.responsible_party_id || undefined,
        remarks: form.remarks || undefined,
      };
      if (editingId) await updateMilestone(editingId, payload);
      else await createMilestone(payload);
      await load();
      toast.success(editingId ? "Milestone updated" : "Milestone created");
      setDialogOpen(false);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to save milestone");
    } finally {
      setSaving(false);
    }
  };

  const onExport = async (format: "csv" | "xlsx" | "pdf") => {
    try {
      const blob = await exportKeyDates(format, projectFilter !== "all" ? { project_id: projectFilter } : undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `key-date-register.${format}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Export failed");
    }
  };

  const remove = async (m: MilestoneDTO) => {
    if (baselineFrozen) {
      toast.error("Frozen Original Key Dates cannot be deleted.");
      return;
    }
    try {
      await deleteMilestone(m.id);
      await load();
      toast.success("Milestone deleted");
    } catch {
      toast.error("Failed to delete milestone");
    }
  };

  const onRecalculate = async () => {
    if (projectFilter === "all") {
      toast.error("Select a project to recalculate its key dates");
      return;
    }
    if (!window.confirm(
      "Re-derive key dates from the contract start date (LOA) and week basis? " +
      "Milestones with an approved EOT revision keep their dates; others are recalculated."
    )) return;
    try {
      const res = await recalculateKeyDates(projectFilter);
      toast.success(`Recalculated ${res.updated} milestone(s) using "${res.week_basis}"`);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Recalculation failed");
    }
  };

  const eotCols = maxRevisionCount(items);

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <CalendarClock className="h-6 w-6 text-blue-600" />
          <div>
            <h1 className="text-2xl font-bold">Key Date / Milestone Tracker</h1>
            <p className="text-sm text-muted-foreground">
              Contractual key dates, EOT extensions and actual achievement records.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onRecalculate}
            disabled={projectFilter === "all" || baselineFrozen}
            title={projectFilter === "all" ? "Select a project first" : "Recalculate key dates from LOA + week basis"}
          >
            <RefreshCw className="mr-2 h-4 w-4" />Recalculate
          </Button>
          <Button variant="outline" size="sm" onClick={() => onExport("csv")}>
            <Download className="mr-2 h-4 w-4" />CSV
          </Button>
          <Button variant="outline" size="sm" onClick={() => setCsvOpen(true)} disabled={baselineFrozen}>
            <Upload className="mr-2 h-4 w-4" />Upload CSV
          </Button>
          <Button variant="outline" size="sm" onClick={() => onExport("xlsx")}>
            <Download className="mr-2 h-4 w-4" />Excel
          </Button>
          <Button variant="outline" size="sm" onClick={() => onExport("pdf")}>
            <Download className="mr-2 h-4 w-4" />PDF
          </Button>
          <Button onClick={openCreate} disabled={baselineFrozen}>
            <PlusCircle className="mr-2 h-4 w-4" />
            New Milestone
          </Button>
        </div>
      </div>

      {dash && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          <Stat label="Total" value={dash.total} />
          <Stat label="Achieved" value={dash.achieved} cls="text-green-600" />
          <Stat label="Overdue" value={dash.overdue} cls="text-red-600" />
          <Stat label="Due ≤30d" value={dash.due_30} cls="text-amber-600" />
          <Stat label="Pending EOTs" value={dash.eot_under_review} cls="text-blue-600" />
          <Stat label="Frozen grants" value={dash.eot_approved} cls="text-purple-600" />
        </div>
      )}

      {projectFilter !== "all" && (
        <KeyDateRevisionWorkflow
          projectId={projectFilter}
          milestones={items}
          onChanged={load}
          onBaselineStatusChange={setBaselineFrozen}
        />
      )}

      <Card>
        <CardHeader>
          <CardTitle>Register</CardTitle>
          <div className="flex flex-wrap gap-3 pt-3">
            <Select value={projectFilter} onValueChange={setProjectFilter}>
              <SelectTrigger className="w-56"><SelectValue placeholder="Project" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All projects</SelectItem>
                {projects.map((p) => (
                  <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-48"><SelectValue placeholder="Status" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {STATUS_OPTIONS.map((s) => (
                  <SelectItem key={s} value={s}>{statusLabel(s)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ref</TableHead>
                <TableHead>Description</TableHead>
                <TableHead className="text-center">Weeks</TableHead>
                <TableHead>Original</TableHead>
                {Array.from({ length: eotCols }, (_, i) => (
                  <TableHead key={`eot-h-${i}`} className="whitespace-nowrap">EOT-{i + 1}</TableHead>
                ))}
                <TableHead>Current</TableHead>
                <TableHead>Latest EOT Submitted</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>EOT</TableHead>
                <TableHead>Achievement</TableHead>
                <TableHead>Alert</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={11 + eotCols} className="py-8 text-center text-muted-foreground">
                    No milestones yet.
                  </TableCell>
                </TableRow>
              ) : (
                items.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell className="font-mono text-xs">{m.milestone_ref || "—"}</TableCell>
                    <TableCell className="max-w-[220px] truncate font-medium" title={m.title}>{m.title}</TableCell>
                    <TableCell className="text-center">{m.contractual_week_number ?? "—"}</TableCell>
                    <TableCell>{fmtDate(m.original_planned_key_date)}</TableCell>
                    {Array.from({ length: eotCols }, (_, i) => {
                      const rev = revisionAt(m, i + 1);
                      return (
                        <TableCell
                          key={`eot-c-${m.id}-${i}`}
                          className="whitespace-nowrap text-xs"
                          title={rev ? [rev.eot_letter_reference, rev.approval_letter_reference].filter(Boolean).join(" → ") : undefined}
                        >
                          {rev ? fmtDate(rev.approved_revised_key_date) : "—"}
                        </TableCell>
                      );
                    })}
                    <TableCell>{fmtDate(m.current_approved_key_date)}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs">
                      <div>
                        {m.latest_eot_submission_label || "—"}
                        {m.latest_eot_submitted_date ? ` · ${fmtDate(m.latest_eot_submitted_date)}` : ""}
                      </div>
                      {m.pending_eot_count ? (
                        <div className="text-muted-foreground">{m.pending_eot_count} pending</div>
                      ) : null}
                    </TableCell>
                    <TableCell><Badge className={statusColor(m.status)}>{statusLabel(m.status)}</Badge></TableCell>
                    <TableCell className="text-xs">{m.latest_eot_status || m.eot_status || "—"}</TableCell>
                    <TableCell className="text-xs">{achievementText(m)}</TableCell>
                    <TableCell className="text-xs">{alertText(m)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" className="h-8 w-8" title="Open" onClick={() => navigate(`/key-dates/${m.id}`)}>
                          <Eye className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-8 w-8" title={baselineFrozen ? "Frozen baseline" : "Edit"} onClick={() => openEdit(m)} disabled={baselineFrozen}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8" title={baselineFrozen ? "Frozen baseline" : "Delete"} disabled={baselineFrozen}>
                              <Trash2 className="h-4 w-4 text-destructive" />
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Delete milestone?</AlertDialogTitle>
                              <AlertDialogDescription>
                                This permanently deletes &quot;{m.title}&quot;.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction onClick={() => remove(m)}>Delete</AlertDialogAction>
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
            <DialogTitle>{editingId ? "Edit Milestone" : "New Milestone"}</DialogTitle>
            <DialogDescription>
              The key date is calculated from the project start date and the contractual week.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>Title</Label>
              <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g. Foundation complete" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Project</Label>
                <Select value={form.project_id} onValueChange={(v) => setForm({ ...form, project_id: v })}>
                  <SelectTrigger><SelectValue placeholder="Select project" /></SelectTrigger>
                  <SelectContent>
                    {projects.map((p) => (
                      <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Reference</Label>
                <Input value={form.milestone_ref} onChange={(e) => setForm({ ...form, milestone_ref: e.target.value })} placeholder="MS-001" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Contractual week no.</Label>
                <Input type="number" min={1} value={form.contractual_week_number} onChange={(e) => setForm({ ...form, contractual_week_number: e.target.value })} />
              </div>
              <div>
                <Label>Project start date</Label>
                <Input type="date" value={form.project_start_date} onChange={(e) => setForm({ ...form, project_start_date: e.target.value })} />
              </div>
            </div>
            <div>
              <Label>Responsible party (user id)</Label>
              <Input value={form.responsible_party_id} onChange={(e) => setForm({ ...form, responsible_party_id: e.target.value })} />
            </div>
            <div>
              <Label>Description</Label>
              <Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={2} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>Cancel</Button>
            <Button onClick={submit} disabled={saving || !form.title.trim()}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlusCircle className="mr-2 h-4 w-4" />}
              {editingId ? "Save changes" : "Create milestone"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CsvImportDialog
        open={csvOpen}
        onOpenChange={setCsvOpen}
        title="Upload Key Dates CSV"
        description="Preview imported milestones and fix row errors before saving."
        sampleFileName="key-date-import-template.csv"
        onDownloadTemplate={downloadKeyDatesImportTemplate}
        onPreview={(file) => previewKeyDatesCsv(file, projectFilter !== "all" ? { project_id: projectFilter } : undefined)}
        onImport={(file) => importKeyDatesCsv(file, projectFilter !== "all" ? { project_id: projectFilter } : undefined)}
        onImported={load}
        rowLabel={(row) => String(row.data?.title || row.data?.milestone_ref || `Row ${row.row_number}`)}
      />
    </div>
  );
};

export default KeyDateRegisterPage;
