import React, { useEffect, useState } from "react";
import { FileText, Loader2, Search, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { errorMessage } from "@/lib/plain-language";
import { type DocumentLabel, documentName, UNAVAILABLE } from "@/lib/picker-labels";
import { type DocumentItem, getDocument, listDocuments } from "@/services/documents-api";

interface Props {
  projectId: string;
  value: string[];
  onChange: (ids: string[]) => void;
  label: string;
  /** Keep only some documents (for example, letters). Applied to search results. */
  filter?: (doc: DocumentItem) => boolean;
  describe?: DocumentLabel;
  placeholder?: string;
  disabled?: boolean;
  /** At most one document. */
  single?: boolean;
}

/**
 * Search a project's documents and choose some (plan 4B-11).
 *
 * The server re-validates every id against the record's organisation and
 * project, so this control is a convenience, not the security boundary.
 * Chosen documents are shown by name; one that cannot be read is shown as
 * "Restricted or removed document", never as its id.
 */
const DocumentPicker: React.FC<Props> = ({
  projectId,
  value,
  onChange,
  label,
  filter,
  describe = documentName,
  placeholder = "Search by name, subject or reference",
  disabled = false,
  single = false,
}) => {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<DocumentItem[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const unknown = value.filter((id) => !(id in names));
    if (unknown.length === 0) return;
    let active = true;
    Promise.all(
      unknown.map((id) =>
        getDocument(id)
          .then((doc) => [id, describe(doc)] as const)
          .catch(() => [id, UNAVAILABLE] as const)
      )
    ).then((pairs) => {
      if (active) setNames((prev) => ({ ...prev, ...Object.fromEntries(pairs) }));
    });
    return () => {
      active = false;
    };
  }, [value, names, describe]);

  const search = async () => {
    if (!projectId) return;
    setSearching(true);
    setError(null);
    try {
      const { documents } = await listDocuments({ project_id: projectId, q: q.trim() || undefined, limit: 50 });
      const list = (documents || []).filter((doc) => Boolean(doc._id || doc.id)).filter((doc) => !filter || filter(doc));
      setResults(list);
      setNames((prev) => {
        const next = { ...prev };
        list.forEach((doc) => {
          next[String(doc._id || doc.id)] = describe(doc);
        });
        return next;
      });
    } catch (err) {
      setResults([]);
      setError(errorMessage(err));
    } finally {
      setSearching(false);
    }
  };

  const choose = (id: string) => {
    if (value.includes(id)) return;
    onChange(single ? [id] : [...value, id]);
  };

  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{label}</p>
      <div className="flex flex-wrap gap-2" aria-label={`Chosen: ${label}`}>
        {value.length === 0 ? (
          <span className="text-sm text-muted-foreground">None chosen.</span>
        ) : (
          value.map((id) => (
            <Badge key={id} variant="neutral" className="gap-1">
              <FileText className="h-3 w-3" />
              {names[id] ?? "Loading"}
              {!disabled && (
                <button type="button" className="ml-1" onClick={() => onChange(value.filter((item) => item !== id))} aria-label={`Remove ${names[id] ?? "document"}`}>
                  <X className="h-3 w-3" />
                </button>
              )}
            </Badge>
          ))
        )}
      </div>
      {!disabled && (
        <div className="flex gap-2">
          <Input
            aria-label={`Search: ${label}`}
            placeholder={projectId ? placeholder : "Choose a project first"}
            value={q}
            disabled={!projectId}
            onChange={(event) => setQ(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void search();
              }
            }}
          />
          <Button type="button" variant="outline" aria-label="Search" onClick={() => void search()} disabled={searching || !projectId}>
            {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {!disabled && results.length > 0 && (
        <ul className="max-h-48 overflow-y-auto rounded-md border" aria-label={`Results: ${label}`}>
          {results.map((doc) => {
            const id = String(doc._id || doc.id);
            const chosen = value.includes(id);
            return (
              <li key={id} className="flex items-center justify-between gap-2 border-b px-3 py-2 last:border-0">
                <span className="truncate text-sm">{describe(doc)}</span>
                <Button type="button" size="sm" variant={chosen ? "ghost" : "outline"} disabled={chosen} onClick={() => choose(id)}>
                  {chosen ? "Chosen" : "Choose"}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export default DocumentPicker;
