import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { FileText, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import type { DocumentItem } from "@/services/documents-api";
import {
  batchLinkDocuments,
  type DocumentRelationship,
  listEntityDocumentLinks,
  removeDocumentLink,
  searchLinkableDocuments,
} from "@/services/document-relationships-api";

export interface RelationshipRoleOption { value: string; label: string }

function documentName(document?: DocumentItem | null, fallback?: string) {
  return document?.filename || document?.name || document?.subject || fallback || "Document";
}

function documentMetadata(document?: DocumentItem | null) {
  if (!document) return "";
  const parts = [document.status, ...(document.categories || [])].filter(Boolean) as string[];
  if (document.createdAt) {
    const created = new Date(document.createdAt);
    if (!Number.isNaN(created.getTime())) {
      parts.push(new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }).format(created));
    }
  }
  return parts.join(" · ");
}

export default function EntityDocumentLinks({
  targetType, targetId, organizationId, projectId, roles, defaultRole,
  frozen = false, canManage = false,
}: {
  targetType: string; targetId: string; organizationId?: string | null;
  projectId?: string | null; roles: ReadonlyArray<RelationshipRoleOption>;
  defaultRole: string; frozen?: boolean; canManage?: boolean;
}) {
  const [links, setLinks] = useState<DocumentRelationship[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<DocumentItem[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [hasMore, setHasMore] = useState(false);
  const [role, setRole] = useState(defaultRole);
  const [busy, setBusy] = useState(false);
  const targetGeneration = useRef(0);
  const searchTimer = useRef<number | undefined>(undefined);
  const loadSequence = useRef(0);
  const searchSequence = useRef(0);

  useEffect(() => {
    targetGeneration.current += 1;
    loadSequence.current += 1;
    searchSequence.current += 1;
    setLinks([]);
    setLoadError(false);
    setSelected(new Set());
    setResults([]);
    setHasMore(false);
    setBusy(false);
  }, [targetType, targetId]);

  const load = useCallback(async () => {
    const generation = targetGeneration.current;
    const sequence = ++loadSequence.current;
    setLoading(true);
    setLoadError(false);
    try {
      const loaded = await listEntityDocumentLinks(targetType, targetId);
      if (generation === targetGeneration.current && sequence === loadSequence.current) {
        setLinks(loaded);
      }
    } catch {
      if (generation === targetGeneration.current && sequence === loadSequence.current) {
        setLinks([]);
        setLoadError(true);
        toast.error("Failed to load linked Documents");
      }
    } finally {
      if (generation === targetGeneration.current && sequence === loadSequence.current) {
        setLoading(false);
      }
    }
  }, [targetId, targetType]);

  useEffect(() => { void load(); }, [load]);

  const linkedDocumentIds = useMemo(
    () => new Set(links.map((link) => link.document_id)), [links],
  );

  const search = useCallback(async (skip = 0, append = false) => {
    const generation = targetGeneration.current;
    const sequence = ++searchSequence.current;
    if (!canManage || frozen || !query.trim()) {
      setResults([]);
      setHasMore(false);
      setBusy(false);
      return;
    }
    setBusy(true);
    try {
      const found = await searchLinkableDocuments({
        q: query.trim(), organization_id: organizationId || undefined,
        project_id: projectId || undefined, limit: 25, skip,
      });
      if (generation === targetGeneration.current && sequence === searchSequence.current) {
        setResults((current) => append ? [...current, ...found] : found);
        setHasMore(found.length === 25);
      }
    } catch {
      if (generation === targetGeneration.current && sequence === searchSequence.current) {
        toast.error("Document search failed");
      }
    }
    finally {
      if (generation === targetGeneration.current && sequence === searchSequence.current) {
        setBusy(false);
      }
    }
  }, [canManage, frozen, organizationId, projectId, query]);

  useEffect(() => {
    if (!query.trim()) {
      searchSequence.current += 1;
      setResults([]);
      setHasMore(false);
      setBusy(false);
      return undefined;
    }
    searchTimer.current = window.setTimeout(() => void search(0, false), 300);
    return () => window.clearTimeout(searchTimer.current);
  }, [query, search]);

  const searchNow = () => {
    window.clearTimeout(searchTimer.current);
    void search(0, false);
  };

  const linkSelected = async (documentIds?: string[]) => {
    const pending = (documentIds || Array.from(selected)).filter(
      (documentId) => !linkedDocumentIds.has(documentId),
    );
    if (!pending.length || busy) return;
    const generation = targetGeneration.current;
    setBusy(true);
    try {
      const linked = await batchLinkDocuments(
        targetType, targetId,
        pending.map((documentId) => ({ document_id: documentId, relationship_role: role })),
      );
      if (generation !== targetGeneration.current) return;
      setLinks((current) => {
        const byId = new Map(current.map((item) => [item._id, item]));
        linked.forEach((item) => byId.set(item._id, item));
        return Array.from(byId.values());
      });
      setSelected(new Set());
      toast.success(`${linked.length} Document${linked.length === 1 ? "" : "s"} linked`);
    } catch {
      if (generation === targetGeneration.current) {
        toast.error("Failed to link Documents");
        await load();
      }
    }
    finally { if (generation === targetGeneration.current) setBusy(false); }
  };

  const unlink = async (link: DocumentRelationship) => {
    if (!canManage || frozen || busy) return;
    setBusy(true);
    const generation = targetGeneration.current;
    try {
      const targetLabel = targetType.charAt(0).toUpperCase() + targetType.slice(1);
      await removeDocumentLink(link._id, link._revision, `Removed from ${targetLabel}`);
      if (generation !== targetGeneration.current) return;
      setLinks((current) => current.filter((item) => item._id !== link._id));
      toast.success("Document unlinked");
    } catch {
      if (generation === targetGeneration.current) {
        toast.error("Failed to unlink Document; reload before retrying");
        await load();
      }
    } finally {
      if (generation === targetGeneration.current) setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      {loading ? (
        <p className="flex items-center text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading linked Documents…
        </p>
      ) : loadError ? (
        <div className="flex items-center gap-2 text-sm text-destructive">
          <span>Linked Documents could not be loaded.</span>
          <Button type="button" variant="outline" size="sm" aria-label="Retry linked Documents"
            onClick={() => void load()}>Retry</Button>
        </div>
      ) : links.length === 0 ? (
        <p className="text-sm text-muted-foreground">No linked Documents yet.</p>
      ) : links.map((link) => {
        const name = documentName(link.document, link.document_id);
        return (
          <div key={link._id} className="flex items-center gap-2 rounded-md border p-2 text-sm">
            <Link to={`/documentviewer/${link.document_id}`} className="flex min-w-0 flex-1 items-center gap-2 hover:underline">
              <FileText className="h-4 w-4 shrink-0" />
              <span className="truncate">{name}</span>
              <span className="text-xs text-muted-foreground">{link.relationship_role}</span>
              {documentMetadata(link.document) && (
                <span className="text-xs text-muted-foreground">{documentMetadata(link.document)}</span>
              )}
            </Link>
            {canManage && !frozen && link.source !== "legacy_read_through" && (
              <Button type="button" variant="ghost" size="sm" aria-label={`Unlink ${name}`}
                disabled={busy} onClick={() => void unlink(link)}>
                <Trash2 className="h-4 w-4" />
              </Button>
            )}
          </div>
        );
      })}

      {frozen ? (
        <p className="text-xs text-muted-foreground">Evidence is frozen for this record.</p>
      ) : !canManage ? (
        <p className="text-xs text-muted-foreground">You have view-only access to document links.</p>
      ) : (
        <div className="space-y-2 border-t pt-3">
          <div className="flex gap-2">
            <label className="sr-only" htmlFor={`${targetType}-document-search`}>Search Documents</label>
            <input id={`${targetType}-document-search`}
              className="h-9 flex-1 rounded-md border border-input bg-background px-3 text-sm"
              value={query} onChange={(event) => setQuery(event.target.value)} />
            <Button type="button" variant="outline" size="sm" disabled={busy} onClick={searchNow}>Search</Button>
          </div>
          <label className="flex items-center gap-2 text-xs" htmlFor={`${targetType}-document-role`}>
            Relationship role
            <select id={`${targetType}-document-role`}
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={role} onChange={(event) => setRole(event.target.value)}>
              {roles.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          {results.map((document) => {
            const name = documentName(document, document._id);
            const alreadyLinked = linkedDocumentIds.has(document._id);
            return (
              <div key={document._id} className="flex items-center justify-between gap-2 rounded-md border p-2 text-sm">
                <label className="flex items-center gap-2">
                  <input type="checkbox" aria-label={`Select ${name}`} disabled={alreadyLinked || busy}
                    checked={selected.has(document._id)} onChange={(event) => setSelected((current) => {
                      const next = new Set(current);
                      if (event.target.checked) next.add(document._id); else next.delete(document._id);
                      return next;
                    })} />
                  <span>
                    <span className="block">{name}</span>
                    {documentMetadata(document) && (
                      <span className="block text-xs text-muted-foreground">{documentMetadata(document)}</span>
                    )}
                  </span>
                </label>
                <div className="flex items-center gap-2">
                  <Link to={`/documentviewer/${document._id}`} aria-label={`View ${name}`}
                    className="text-xs text-primary hover:underline">View</Link>
                  {alreadyLinked ? <span className="text-xs text-muted-foreground">Already linked</span> : (
                    <Button type="button" variant="outline" size="sm" disabled={busy}
                      aria-label={`Link ${name}`} onClick={() => void linkSelected([document._id])}>Link</Button>
                  )}
                </div>
              </div>
            );
          })}
          {selected.size > 0 && (
            <Button type="button" disabled={busy} onClick={() => void linkSelected()}>
              Link selected ({selected.size})
            </Button>
          )}
          {hasMore && (
            <Button type="button" variant="outline" disabled={busy}
              onClick={() => void search(results.length, true)}>Load more</Button>
          )}
        </div>
      )}
    </div>
  );
}
