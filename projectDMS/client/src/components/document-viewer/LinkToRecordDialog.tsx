import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  defaultRoleFor,
  LINK_TO_RECORD_TARGETS,
  relationshipRoleLabel,
} from "@/lib/relationship-roles";
import {
  batchLinkDocuments,
  type DocumentLinkTarget,
  listDocumentLinkTargets,
} from "@/services/document-relationships-api";
import { scopeErrorCode } from "@/services/active-scope";

/**
 * Variation, Hindrance, Programme Milestone and Chronology are held to the navbar
 * selection (CL-3A/CL-3B); say so plainly.
 */
function scopeMessage(error: unknown): string | null {
  const code = scopeErrorCode(error);
  if (code === "selection_required") return "Select a project in the navbar to link this register.";
  if (code === "context_forbidden") return "This record is not in the project selected in the navbar.";
  return null;
}

/**
 * "Link to Record" from the Document side: pick a register type, a record and a
 * relationship role. The server offers only records in the Document's own
 * project on which the caller holds the manage permission, and re-authorizes
 * the write. The dialog stays open after a link so one letter can be linked to
 * several records.
 */
export default function LinkToRecordDialog({
  documentId, open, onOpenChange, linkedKeys, targetTypes, onLinked,
}: {
  documentId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `${target_type}:${target_id}:${role}` of links that already exist. */
  linkedKeys: ReadonlySet<string>;
  /** Register types this caller may link to (`GET /documents/{id}/link-target-types`). */
  targetTypes: ReadonlyArray<string>;
  onLinked: () => void;
}) {
  const registers = useMemo(
    () => LINK_TO_RECORD_TARGETS.filter((item) => targetTypes.includes(item.value)),
    [targetTypes],
  );
  const [targetType, setTargetType] = useState(registers[0]?.value ?? "");
  useEffect(() => {
    if (!registers.some((item) => item.value === targetType)) setTargetType(registers[0]?.value ?? "");
  }, [registers, targetType]);
  const [query, setQuery] = useState("");
  const [targets, setTargets] = useState<DocumentLinkTarget[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [role, setRole] = useState("");
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);

  useEffect(() => {
    if (!open || !targetType) return undefined;
    const current = ++sequence.current;
    setLoading(true);
    setLoadError(null);
    const timer = window.setTimeout(() => {
      listDocumentLinkTargets(documentId, {
        target_type: targetType,
        ...(query.trim() ? { q: query.trim() } : {}),
      })
        .then((rows) => {
          if (current !== sequence.current) return;
          setTargets(rows);
          setSelectedId((previous) => (rows.some((row) => row.target_id === previous) ? previous : null));
        })
        .catch((error: unknown) => {
          if (current !== sequence.current) return;
          setTargets([]);
          setLoadError(scopeMessage(error) || "Records could not be loaded.");
        })
        .finally(() => {
          if (current === sequence.current) setLoading(false);
        });
    }, query.trim() ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [documentId, open, query, targetType]);

  const selected = useMemo(
    () => targets.find((row) => row.target_id === selectedId) || null,
    [selectedId, targets],
  );

  useEffect(() => {
    setRole(selected ? defaultRoleFor(selected.allowed_roles) : "");
  }, [selected]);

  const alreadyLinked = Boolean(
    selected && linkedKeys.has(`${selected.target_type}:${selected.target_id}:${role}`),
  );

  const link = async () => {
    if (!selected || !role || busy || selected.frozen || alreadyLinked) return;
    setBusy(true);
    try {
      await batchLinkDocuments(selected.target_type, selected.target_id, [
        { document_id: documentId, relationship_role: role },
      ]);
      toast.success(`Linked to ${selected.label}`);
      onLinked();
    } catch (error: unknown) {
      const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      toast.error(scopeMessage(error) || (typeof detail === "string" && detail ? detail : "Failed to link this Document"));
    } finally {
      setBusy(false);
    }
  };

  const inputClass = "h-9 rounded-md border border-input bg-background px-3 text-sm";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Link to Record</DialogTitle>
          <DialogDescription>
            Link this Document to a register record in its own project. You can link it to
            several records.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <label className="flex flex-col gap-1 text-xs" htmlFor="link-to-record-type">
            Register
            <select id="link-to-record-type" className={inputClass} value={targetType}
              onChange={(event) => { setTargetType(event.target.value); setSelectedId(null); }}>
              {registers.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs" htmlFor="link-to-record-search">
            Find record
            <input id="link-to-record-search" className={inputClass} value={query}
              placeholder="Reference or number" onChange={(event) => setQuery(event.target.value)} />
          </label>
          <div className="max-h-56 space-y-1 overflow-y-auto" role="radiogroup" aria-label="Records">
            {loading ? (
              <p className="flex items-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading records…
              </p>
            ) : loadError ? (
              <p role="alert" className="text-sm text-destructive">{loadError}</p>
            ) : targets.length === 0 ? (
              <p className="text-sm text-muted-foreground">No records you can link in this project.</p>
            ) : targets.map((target) => (
              <label key={target.target_id}
                className="flex items-center gap-2 rounded-md border p-2 text-sm">
                <input type="radio" name="link-to-record-target" value={target.target_id}
                  aria-label={target.label} disabled={target.frozen}
                  checked={selectedId === target.target_id}
                  onChange={() => setSelectedId(target.target_id)} />
                <span className="flex-1">{target.label}</span>
                {target.frozen && <span className="text-xs text-muted-foreground">Evidence frozen</span>}
              </label>
            ))}
          </div>
          {selected && (
            <label className="flex flex-col gap-1 text-xs" htmlFor="link-to-record-role">
              Relationship role
              <select id="link-to-record-role" className={inputClass} value={role}
                onChange={(event) => setRole(event.target.value)}>
                {selected.allowed_roles.map((value) => (
                  <option key={value} value={value}>{relationshipRoleLabel(value)}</option>
                ))}
              </select>
            </label>
          )}
          <div className="flex items-center justify-end gap-2">
            {alreadyLinked && <span className="text-xs text-muted-foreground">Already linked</span>}
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Done</Button>
            <Button type="button" disabled={!selected || !role || busy || alreadyLinked || selected.frozen}
              onClick={() => void link()}>
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Link
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
