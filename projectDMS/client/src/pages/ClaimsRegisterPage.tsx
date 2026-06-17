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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Edit, Loader2, PlusCircle, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  ClaimDTO,
  ClaimPayload,
  ClaimStatus,
  ClaimType,
  createClaim,
  deleteClaim,
  getClaims,
  setClaimStatus,
  updateClaim,
} from "@/services/claims-api";
import { enhancedApi } from "@/services/enhanced-api";
import ClaimApprovalDialog from "@/components/claims/ClaimApprovalDialog";

const CLAIM_TYPES: { value: ClaimType; label: string }[] = [
  { value: "eot", label: "Extension of Time" },
  { value: "variation", label: "Variation" },
  { value: "payment_ipc", label: "Payment / IPC" },
  { value: "loss_expense", label: "Loss & Expense" },
  { value: "acceleration", label: "Acceleration" },
  { value: "defect", label: "Defect" },
  { value: "other", label: "Other" },
];

const CLAIM_STATUSES: { value: ClaimStatus; label: string }[] = [
  { value: "draft", label: "Draft" },
  { value: "notified", label: "Notified" },
  { value: "submitted", label: "Submitted" },
  { value: "under_review", label: "Under Review" },
  { value: "agreed", label: "Agreed" },
  { value: "rejected", label: "Rejected" },
  { value: "disputed", label: "Disputed" },
  { value: "closed", label: "Closed" },
];

const STATUS_COLOR: Record<ClaimStatus, string> = {
  draft: "bg-gray-500",
  notified: "bg-blue-500",
  submitted: "bg-indigo-500",
  under_review: "bg-amber-500",
  agreed: "bg-green-600",
  rejected: "bg-red-500",
  disputed: "bg-red-700",
  closed: "bg-gray-700",
};

const typeLabel = (t: ClaimType) => CLAIM_TYPES.find((x) => x.value === t)?.label || t;
const fmtDate = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");
const fmtAmount = (n?: number | null) => (n != null ? n.toLocaleString() : "—");

interface ClaimForm {
  title: string;
  type: ClaimType;
  claim_ref: string;
  description: string;
  amount_claimed?: number;
  response_due_date: string;
  project_id: string;
  status: ClaimStatus;
}

const EMPTY_FORM: ClaimForm = {
  title: "",
  type: "other",
  claim_ref: "",
  description: "",
  amount_claimed: undefined,
  response_due_date: "",
  project_id: "",
  status: "draft",
};

const ClaimsRegisterPage: React.FC = () => {
  const [claims, setClaims] = useState<ClaimDTO[]>([]);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ClaimForm>({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);
  const [approvalClaim, setApprovalClaim] = useState<ClaimDTO | null>(null);

  const load = useCallback(async () => {
    try {
      const params: Record<string, string> = {};
      if (typeFilter !== "all") params.type = typeFilter;
      if (statusFilter !== "all") params.status = statusFilter;
      setClaims(await getClaims(params));
    } catch {
      toast.error("Failed to load claims");
    }
  }, [typeFilter, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const ps = await enhancedApi.getProjects();
        if (active) {
          setProjects(
            (ps || []).map((p: any) => ({ id: String(p.id || p._id || ""), name: p.name || "Project" }))
          );
        }
      } catch {
        /* projects optional */
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const openCreate = () => {
    setEditingId(null);
    setForm({ ...EMPTY_FORM });
    setDialogOpen(true);
  };

  const openEdit = (c: ClaimDTO) => {
    setEditingId(c.id);
    setForm({
      title: c.title,
      type: c.type,
      claim_ref: c.claim_ref || "",
      description: c.description || "",
      amount_claimed: c.amount_claimed ?? undefined,
      response_due_date: c.response_due_date ? c.response_due_date.slice(0, 10) : "",
      project_id: c.project_id || "",
      status: c.status,
    });
    setDialogOpen(true);
  };

  const submit = async () => {
    if (!form.title.trim()) {
      toast.error("Title is required");
      return;
    }
    try {
      setSaving(true);
      const payload: ClaimPayload = {
        title: form.title.trim(),
        type: form.type,
        claim_ref: form.claim_ref || undefined,
        description: form.description || undefined,
        amount_claimed: form.amount_claimed,
        response_due_date: form.response_due_date
          ? new Date(form.response_due_date).toISOString()
          : undefined,
        project_id: form.project_id || undefined,
        status: form.status,
      };
      if (editingId) await updateClaim(editingId, payload);
      else await createClaim(payload);
      await load();
      toast.success(editingId ? "Claim updated" : "Claim created");
      setDialogOpen(false);
    } catch {
      toast.error("Failed to save claim");
    } finally {
      setSaving(false);
    }
  };

  const changeStatus = async (c: ClaimDTO, statusValue: ClaimStatus) => {
    try {
      await setClaimStatus(c.id, statusValue);
      await load();
    } catch {
      toast.error("Failed to update status");
    }
  };

  const remove = async (c: ClaimDTO) => {
    try {
      await deleteClaim(c.id);
      await load();
      toast.success("Claim deleted");
    } catch {
      toast.error("Failed to delete claim");
    }
  };

  return (
    <div className="container mx-auto p-6">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Claim Register</h1>
        <Button onClick={openCreate}>
          <PlusCircle className="mr-2 h-4 w-4" />
          New Claim
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Claims</CardTitle>
          <CardDescription>Track EOT, variation, payment and loss/expense claims.</CardDescription>
          <div className="flex gap-3 pt-3">
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="w-48"><SelectValue placeholder="Type" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All types</SelectItem>
                {CLAIM_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-48"><SelectValue placeholder="Status" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {CLAIM_STATUSES.map((s) => (
                  <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
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
                <TableHead>Title</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Amount</TableHead>
                <TableHead>Response due</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {claims.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-muted-foreground py-8">
                    No claims yet.
                  </TableCell>
                </TableRow>
              ) : (
                claims.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-mono text-xs">{c.claim_ref || "—"}</TableCell>
                    <TableCell className="font-medium">{c.title}</TableCell>
                    <TableCell>{typeLabel(c.type)}</TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button type="button" className="cursor-pointer">
                            <Badge className={STATUS_COLOR[c.status]}>
                              {CLAIM_STATUSES.find((s) => s.value === c.status)?.label || c.status}
                            </Badge>
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start">
                          {CLAIM_STATUSES.map((s) => (
                            <DropdownMenuItem key={s.value} onClick={() => changeStatus(c, s.value)}>
                              {s.label}
                            </DropdownMenuItem>
                          ))}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                    <TableCell>{fmtAmount(c.amount_claimed)}</TableCell>
                    <TableCell>{fmtDate(c.response_due_date)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" className="h-8 w-8" title="Approval workflow" onClick={() => setApprovalClaim(c)}>
                          <ShieldCheck className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-8 w-8" title="Edit" onClick={() => openEdit(c)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8" title="Delete">
                              <Trash2 className="h-4 w-4 text-destructive" />
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Delete claim?</AlertDialogTitle>
                              <AlertDialogDescription>
                                This permanently deletes &quot;{c.title}&quot;.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction onClick={() => remove(c)}>Delete</AlertDialogAction>
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
            <DialogTitle>{editingId ? "Edit Claim" : "New Claim"}</DialogTitle>
            <DialogDescription>Capture the claim details and link it to a project.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>Title</Label>
              <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g. EOT — monsoon delay" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Type</Label>
                <Select value={form.type} onValueChange={(v) => setForm({ ...form, type: v as ClaimType })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CLAIM_TYPES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Reference</Label>
                <Input value={form.claim_ref} onChange={(e) => setForm({ ...form, claim_ref: e.target.value })} placeholder="MRP3/CON/EOT/001" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Amount claimed</Label>
                <Input
                  type="number"
                  value={form.amount_claimed ?? ""}
                  onChange={(e) =>
                    setForm({ ...form, amount_claimed: e.target.value === "" ? undefined : Number(e.target.value) })
                  }
                />
              </div>
              <div>
                <Label>Response due</Label>
                <Input type="date" value={form.response_due_date} onChange={(e) => setForm({ ...form, response_due_date: e.target.value })} />
              </div>
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
              {editingId && (
                <div>
                  <Label>Status</Label>
                  <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v as ClaimStatus })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {CLAIM_STATUSES.map((s) => (
                        <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>
            <div>
              <Label>Description</Label>
              <Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>Cancel</Button>
            <Button onClick={submit} disabled={saving || !form.title.trim()}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlusCircle className="mr-2 h-4 w-4" />}
              {editingId ? "Save changes" : "Create claim"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ClaimApprovalDialog
        claimId={approvalClaim?.id ?? null}
        claimTitle={approvalClaim?.title}
        open={approvalClaim !== null}
        onOpenChange={(o) => {
          if (!o) setApprovalClaim(null);
        }}
      />
    </div>
  );
};

export default ClaimsRegisterPage;
