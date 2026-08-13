import React, { useEffect, useState } from "react";
import { FileText, Loader2, Search, X } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { DocumentItem, listDocuments } from "@/services/documents-api";

const docName = (d: DocumentItem) => d.name || d.filename || d._id;

interface Props {
  projectId: string;
  linkedIds: string[];
  onChange: (ids: string[]) => void;
  title?: string;
  /** Read-only rendering, e.g. once an EOT submission is locked. */
  disabled?: boolean;
  emptyHint?: string;
}

/**
 * Search a project's uploaded documents and link them to a record.
 *
 * The server re-validates every id against the record's own organisation and
 * project, so this control is a convenience, not the security boundary.
 */
const LinkedDocumentsPicker: React.FC<Props> = ({
  projectId,
  linkedIds,
  onChange,
  title = "Linked letters & documents",
  disabled = false,
  emptyHint = "Select a project to search its letters.",
}) => {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<DocumentItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [names, setNames] = useState<Record<string, string>>({});

  // Build a name map for already-linked ids (from the project's documents).
  useEffect(() => {
    if (!projectId) return;
    let active = true;
    (async () => {
      try {
        const { documents } = await listDocuments({ project_id: projectId, limit: 200 });
        if (!active) return;
        const map: Record<string, string> = {};
        (documents || []).forEach((d) => { map[d._id] = docName(d); });
        setNames((prev) => ({ ...map, ...prev }));
      } catch { /* optional */ }
    })();
    return () => { active = false; };
  }, [projectId]);

  const search = async () => {
    setSearching(true);
    try {
      const { documents } = await listDocuments({ project_id: projectId || undefined, q: q.trim() || undefined, limit: 25 });
      const term = q.trim().toLowerCase();
      const list = (documents || []).filter((d) => !term || docName(d).toLowerCase().includes(term));
      setResults(list);
      setNames((prev) => { const m = { ...prev }; list.forEach((d) => { m[d._id] = docName(d); }); return m; });
    } catch { toast.error("Search failed"); } finally { setSearching(false); }
  };

  const add = (d: DocumentItem) => { if (!linkedIds.includes(d._id)) onChange([...linkedIds, d._id]); };
  const remove = (id: string) => onChange(linkedIds.filter((x) => x !== id));

  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle className="text-sm">{title}</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {linkedIds.length === 0 ? (
            <span className="text-sm text-muted-foreground">No documents linked.</span>
          ) : linkedIds.map((id) => (
            <Badge key={id} variant="secondary" className="gap-1">
              <FileText className="h-3 w-3" />{names[id] || id}
              {!disabled && (
                <button type="button" className="ml-1" onClick={() => remove(id)} aria-label="Unlink"><X className="h-3 w-3" /></button>
              )}
            </Badge>
          ))}
        </div>
        {!disabled && (
          <div className="flex gap-2">
            <Input placeholder="Search uploaded letters…" value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void search(); } }} />
            <Button type="button" variant="outline" onClick={() => void search()} disabled={searching || !projectId}>
              {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            </Button>
          </div>
        )}
        {!projectId && !disabled && <p className="text-xs text-muted-foreground">{emptyHint}</p>}
        {!disabled && results.length > 0 && (
          <div className="max-h-48 overflow-y-auto rounded-md border">
            {results.map((d) => {
              const linked = linkedIds.includes(d._id);
              return (
                <div key={d._id} className="flex items-center justify-between gap-2 border-b px-3 py-2 last:border-0">
                  <div className="flex min-w-0 items-center gap-2 text-sm">
                    <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <span className="truncate">{docName(d)}</span>
                  </div>
                  <Button type="button" size="sm" variant={linked ? "ghost" : "outline"} disabled={linked} onClick={() => add(d)}>
                    {linked ? "Linked" : "Link"}
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default LinkedDocumentsPicker;
