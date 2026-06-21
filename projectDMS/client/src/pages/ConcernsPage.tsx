import React, { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertCircle, Edit, Loader2, MessageSquareWarning, PlusCircle, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import useRBAC from "@/hooks/useRBAC";
import {
  ConcernDTO,
  ConcernPriority,
  ConcernStatus,
  createConcern,
  deleteConcern,
  getConcerns,
  updateConcern,
} from "@/services/concerns-api";

const STATUSES: ConcernStatus[] = ["open", "in_progress", "resolved", "closed"];
const PRIORITIES: ConcernPriority[] = ["low", "medium", "high", "critical"];

const statusColor: Record<ConcernStatus, string> = {
  open: "bg-blue-100 text-blue-800",
  in_progress: "bg-amber-100 text-amber-800",
  resolved: "bg-green-100 text-green-800",
  closed: "bg-gray-100 text-gray-700",
};
const priorityColor: Record<ConcernPriority, string> = {
  low: "bg-gray-100 text-gray-700",
  medium: "bg-sky-100 text-sky-800",
  high: "bg-orange-100 text-orange-800",
  critical: "bg-red-100 text-red-800",
};
const label = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const fmtDate = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");

interface CForm {
  name: string;
  email: string;
  description: string;
  party_id: string;
  status: ConcernStatus;
  priority: ConcernPriority;
}
const EMPTY: CForm = {
  name: "",
  email: "",
  description: "",
  party_id: "",
  status: "open",
  priority: "medium",
};

const ConcernsPage = () => {
  const { can } = useRBAC();
  const [items, setItems] = useState<ConcernDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<CForm>({ ...EMPTY });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = {};
      if (search.trim()) params.search = search.trim();
      if (statusFilter !== "all") params.status = statusFilter;
      if (priorityFilter !== "all") params.priority = priorityFilter;
      const res = await getConcerns(params);
      setItems(res.concerns);
    } catch {
      toast.error("Failed to load concerns");
    } finally {
      setLoading(false);
    }
  }, [search, statusFilter, priorityFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditingId(null);
    setForm({ ...EMPTY });
    setDialogOpen(true);
  };

  const openEdit = (c: ConcernDTO) => {
    setEditingId(c.id);
    setForm({
      name: c.name || "",
      email: c.email || "",
      description: c.description || "",
      party_id: c.party_id || "",
      status: c.status,
      priority: c.priority,
    });
    setDialogOpen(true);
  };

  const onSave = async () => {
    if (!form.name.trim() && !form.description.trim()) {
      toast.error("Add a name or a description for the concern.");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim() || undefined,
        email: form.email.trim() || undefined,
        description: form.description.trim() || undefined,
        party_id: form.party_id.trim() || undefined,
        status: form.status,
        priority: form.priority,
      };
      if (editingId) {
        await updateConcern(editingId, payload);
        toast.success("Concern updated");
      } else {
        await createConcern(payload);
        toast.success("Concern created");
      }
      setDialogOpen(false);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || "Failed to save concern");
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async (c: ConcernDTO) => {
    if (!window.confirm(`Delete this concern${c.name ? ` from ${c.name}` : ""}?`)) return;
    try {
      await deleteConcern(c.id);
      toast.success("Concern deleted");
      setItems((prev) => prev.filter((x) => x.id !== c.id));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || "Failed to delete concern");
    }
  };

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <MessageSquareWarning className="h-6 w-6 text-blue-600" />
          <div>
            <h1 className="text-2xl font-bold">Concerns</h1>
            <p className="text-sm text-muted-foreground">
              Track stakeholder concerns and their resolution.
            </p>
          </div>
        </div>
        {can("concerns:create") && (
          <Button onClick={openCreate}>
            <PlusCircle className="mr-2 h-4 w-4" />
            Add Concern
          </Button>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Concern Register</CardTitle>
          <div className="flex flex-wrap gap-3 pt-3">
            <Input
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-56"
            />
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-40">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {STATUSES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {label(s)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={priorityFilter} onValueChange={setPriorityFilter}>
              <SelectTrigger className="w-40">
                <SelectValue placeholder="Priority" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All priorities</SelectItem>
                {PRIORITIES.map((p) => (
                  <SelectItem key={p} value={p}>
                    {label(p)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading…
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <AlertCircle className="mb-2 h-8 w-8" />
              No concerns found.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>From</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell>
                      <div className="font-medium">{c.name || "—"}</div>
                      {c.email && (
                        <div className="text-xs text-muted-foreground">{c.email}</div>
                      )}
                    </TableCell>
                    <TableCell className="max-w-md truncate" title={c.description || ""}>
                      {c.description || "—"}
                    </TableCell>
                    <TableCell>
                      <Badge className={statusColor[c.status]} variant="secondary">
                        {label(c.status)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge className={priorityColor[c.priority]} variant="secondary">
                        {label(c.priority)}
                      </Badge>
                    </TableCell>
                    <TableCell>{fmtDate(c.created_at)}</TableCell>
                    <TableCell className="text-right">
                      {can("concerns:update") && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          title="Edit"
                          onClick={() => openEdit(c)}
                        >
                          <Edit className="h-4 w-4" />
                        </Button>
                      )}
                      {can("concerns:delete") && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 text-destructive"
                          title="Delete"
                          onClick={() => onDelete(c)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-[520px]">
          <DialogHeader>
            <DialogTitle>{editingId ? "Edit Concern" : "Add Concern"}</DialogTitle>
            <DialogDescription>
              Record a stakeholder concern and its current status.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label htmlFor="c-name">Name</Label>
              <Input
                id="c-name"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="Raised by"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="c-email">Email</Label>
              <Input
                id="c-email"
                type="email"
                value={form.email}
                onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                placeholder="name@example.com"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="c-desc">Description</Label>
              <Textarea
                id="c-desc"
                rows={4}
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="Describe the concern…"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label>Status</Label>
                <Select
                  value={form.status}
                  onValueChange={(v) => setForm((f) => ({ ...f, status: v as ConcernStatus }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {STATUSES.map((s) => (
                      <SelectItem key={s} value={s}>
                        {label(s)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label>Priority</Label>
                <Select
                  value={form.priority}
                  onValueChange={(v) => setForm((f) => ({ ...f, priority: v as ConcernPriority }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PRIORITIES.map((p) => (
                      <SelectItem key={p} value={p}>
                        {label(p)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={onSave} disabled={saving}>
              {saving ? "Saving…" : editingId ? "Save Changes" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default ConcernsPage;
