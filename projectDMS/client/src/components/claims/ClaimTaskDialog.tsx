import React, { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ClipboardList, Loader2 } from "lucide-react";
import { toast } from "sonner";
import type { ClaimDTO } from "@/services/claims-api";
import { createTask } from "@/services/tasks-api";
import { followUpTaskTitle } from "@/lib/claims-helpers";

interface Props {
  claim: ClaimDTO | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: () => void;
}

/** Raise a follow-up task linked to a claim (Phase 2 — Claims ↔ Tasks). */
const ClaimTaskDialog: React.FC<Props> = ({ claim, open, onOpenChange, onCreated }) => {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open && claim) {
      setTitle(followUpTaskTitle(claim));
      setDescription("");
      setDueDate(claim.response_due_date ? claim.response_due_date.slice(0, 10) : "");
    }
  }, [open, claim]);

  const submit = async () => {
    if (!claim || !title.trim()) return;
    setSaving(true);
    try {
      await createTask({
        title: title.trim(),
        description: description.trim() || undefined,
        linked_claim_id: claim.id,
        project_id: claim.project_id || undefined,
        due_date: dueDate ? new Date(dueDate).toISOString() : undefined,
      });
      toast.success("Task created and linked to claim");
      onOpenChange(false);
      onCreated?.();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to create task");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ClipboardList className="h-5 w-5" />
            New task from claim
          </DialogTitle>
          <DialogDescription>{claim?.title || claim?.id}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>Title</Label>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div>
            <Label>Description</Label>
            <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
          </div>
          <div>
            <Label>Due date</Label>
            <Input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving || !title.trim()}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ClipboardList className="mr-2 h-4 w-4" />}
            Create task
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ClaimTaskDialog;
