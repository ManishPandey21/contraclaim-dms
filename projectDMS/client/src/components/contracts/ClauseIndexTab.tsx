import React, { useCallback, useEffect, useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import Badge from "@/components/ui/badge";
import { toast } from "sonner";
import {
  Loader2,
  ExternalLink,
  Pencil,
  CheckCircle2,
  Ban,
  RefreshCw,
  Scissors,
  GitMerge,
} from "lucide-react";
import {
  listDocumentClauses,
  updateClause,
  regenerateClauseEmbedding,
  splitClause,
  mergeClauses,
  type ClauseRow,
} from "@/services/contracts-api";
import { extractErrorMessage } from "@/lib/error-logger";

interface Props {
  documentId: string;
  onOpenPage?: (page: number) => void;
}

const ClauseIndexTab: React.FC<Props> = ({ documentId, onOpenPage }) => {
  const [clauses, setClauses] = useState<ClauseRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!documentId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await listDocumentClauses(documentId);
      setClauses(data.clauses || []);
    } catch (err) {
      setError(extractErrorMessage(err, "Failed to load clauses."));
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    void load();
    setSelected(new Set());
  }, [load]);

  const applyRow = (row: ClauseRow) =>
    setClauses((prev) => prev.map((c) => (c.clause_uid === row.clause_uid ? { ...c, ...row } : c)));

  const runAction = useCallback(
    async (uid: string, label: string, fn: () => Promise<void>) => {
      setBusy(uid);
      try {
        await fn();
        toast.success(label);
      } catch (err) {
        toast.error(`${label} failed`, { description: extractErrorMessage(err) });
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const onEditTitle = (row: ClauseRow) => {
    const next = window.prompt("Edit clause title", row.clause_title || "");
    if (next === null || next === row.clause_title) return;
    void runAction(row.clause_uid, "Title updated", async () => {
      applyRow(await updateClause(row.clause_uid, { clause_title: next }));
    });
  };

  const onVerify = (row: ClauseRow) =>
    void runAction(row.clause_uid, "Marked verified", async () => {
      applyRow(await updateClause(row.clause_uid, { mark_verified: true }));
    });

  const onSupersede = (row: ClauseRow) =>
    void runAction(row.clause_uid, row.is_superseded ? "Restored as current" : "Marked superseded", async () => {
      applyRow(await updateClause(row.clause_uid, { is_superseded: !row.is_superseded }));
    });

  const onRegenerate = (row: ClauseRow) =>
    void runAction(row.clause_uid, "Embedding queued", async () => {
      applyRow(await regenerateClauseEmbedding(row.clause_uid));
    });

  const onSplit = (row: ClauseRow) => {
    const raw = window.prompt("Split at character offset", "");
    const at = Number(raw);
    if (!raw || !Number.isFinite(at) || at <= 0) return;
    void runAction(row.clause_uid, "Clause split", async () => {
      await splitClause(row.clause_uid, at);
      await load();
    });
  };

  const onMerge = () => {
    const uids = [...selected];
    if (uids.length < 2) return;
    void runAction("merge", "Clauses merged", async () => {
      await mergeClauses(uids);
      setSelected(new Set());
      await load();
    });
  };

  const toggle = (uid: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(uid) ? next.delete(uid) : next.add(uid);
      return next;
    });

  const confidenceVariant = (c?: string | null) =>
    c === "high" ? "success" : c === "medium" ? "warning" : c === "low" ? "danger" : "neutral";

  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading clause index…
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-6 text-center">
        <p className="text-sm text-red-600">{error}</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={() => void load()}>
          Retry
        </Button>
      </div>
    );
  }
  if (!clauses.length) {
    return (
      <div className="p-8 text-center text-sm text-muted-foreground">
        No clause records yet. Run clause indexing on this contract to populate the Clause Index.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between px-1">
        <p className="text-sm text-muted-foreground">{clauses.length} clause records</p>
        <Button size="sm" variant="outline" className="gap-2" disabled={selected.size < 2} onClick={onMerge}>
          <GitMerge className="h-4 w-4" /> Merge selected ({selected.size})
        </Button>
      </div>
      <div className="overflow-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Clause No.</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Page</TableHead>
              <TableHead>Confidence</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Embedding</TableHead>
              <TableHead>Review</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {clauses.map((row) => (
              <TableRow key={row.clause_uid} className={row.is_superseded ? "opacity-60" : ""}>
                <TableCell>
                  <input
                    type="checkbox"
                    className="h-4 w-4"
                    checked={selected.has(row.clause_uid)}
                    onChange={() => toggle(row.clause_uid)}
                  />
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {row.clause_no || (row.chunk_type === "table" ? "TABLE" : "—")}
                  {row.chunk_total && row.chunk_total > 1 ? ` (${row.chunk_part}/${row.chunk_total})` : ""}
                </TableCell>
                <TableCell className="max-w-[220px] truncate" title={row.clause_title || ""}>
                  {row.clause_title || row.table_title || "—"}
                </TableCell>
                <TableCell className="text-xs">{row.document_type || "—"}</TableCell>
                <TableCell className="text-xs">
                  {row.page_start ? (
                    <button
                      type="button"
                      className="text-blue-600 hover:underline"
                      onClick={() => onOpenPage?.(row.page_start as number)}
                      title="Open source page"
                    >
                      {row.page_start}
                      {row.page_end && row.page_end !== row.page_start ? `–${row.page_end}` : ""}
                    </button>
                  ) : (
                    "—"
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={confidenceVariant(row.confidence)}>{row.confidence || "—"}</Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={row.is_superseded ? "warning" : "success"}>
                    {row.is_superseded ? "Superseded" : "Current"}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">{row.embedding_status || "—"}</TableCell>
                <TableCell>
                  <Badge variant={row.quality_status === "validated" ? "success" : "warning"}>
                    {row.quality_status === "validated"
                      ? row.verified_by
                        ? "Verified"
                        : "Validated"
                      : "Needs review"}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="flex items-center justify-end gap-1">
                    {row.page_start ? (
                      <IconBtn title="Open source page" onClick={() => onOpenPage?.(row.page_start as number)}>
                        <ExternalLink className="h-3.5 w-3.5" />
                      </IconBtn>
                    ) : null}
                    <IconBtn title="Edit title" onClick={() => onEditTitle(row)}>
                      <Pencil className="h-3.5 w-3.5" />
                    </IconBtn>
                    <IconBtn title="Mark verified" onClick={() => onVerify(row)} disabled={busy === row.clause_uid}>
                      <CheckCircle2 className="h-3.5 w-3.5" />
                    </IconBtn>
                    <IconBtn
                      title={row.is_superseded ? "Restore as current" : "Mark superseded"}
                      onClick={() => onSupersede(row)}
                      disabled={busy === row.clause_uid}
                    >
                      <Ban className="h-3.5 w-3.5" />
                    </IconBtn>
                    <IconBtn title="Regenerate embedding" onClick={() => onRegenerate(row)} disabled={busy === row.clause_uid}>
                      <RefreshCw className="h-3.5 w-3.5" />
                    </IconBtn>
                    <IconBtn title="Split clause" onClick={() => onSplit(row)} disabled={busy === row.clause_uid}>
                      <Scissors className="h-3.5 w-3.5" />
                    </IconBtn>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
};

const IconBtn: React.FC<{
  title: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}> = ({ title, onClick, disabled, children }) => (
  <button
    type="button"
    title={title}
    onClick={onClick}
    disabled={disabled}
    className="rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-800 disabled:opacity-40"
  >
    {children}
  </button>
);

export default ClauseIndexTab;
