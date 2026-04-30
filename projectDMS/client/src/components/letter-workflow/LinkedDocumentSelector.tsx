import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Separator } from "@/components/ui/separator";
import { toast } from "sonner";
import {
  ExternalLink,
  FileText,
  GitBranch,
  RefreshCcw,
  Search,
} from "lucide-react";
import {
  fetchContextDocuments,
  saveContextDocuments,
  ContextDocumentSummary,
  ContextDocumentsResponse,
} from "@/services/letter-workflow-api";
import { listDocuments, DocumentItem } from "@/services/documents-api";
import { formatDate } from "@/utils/dateFormat";
import type { LanggraphGraphThreadNode } from "@/types/langgraph";

interface LinkedDocumentSelectorProps {
  letterId: string;
  organizationId?: string;
  projectId?: string;
  conversationThread?: LanggraphGraphThreadNode[];
  activeLetterCode?: string;
  onSelectionChange?: (
    ids: string[],
    documents: ContextDocumentSummary[]
  ) => void;
}

const toSummary = (item: DocumentItem): ContextDocumentSummary => {
  const anyItem = item as DocumentItem & {
    letterNo?: string;
    subject?: string;
    uploadType?: string;
    date?: string;
  };
  return {
    id: String(anyItem._id ?? ""),
    letterNo: anyItem.letterNo,
    subject:
      anyItem.subject ??
      anyItem.filename ??
      anyItem.name ??
      anyItem.upload_id ??
      "Document",
    uploadType: anyItem.uploadType,
    date: anyItem.createdAt ?? anyItem.updatedAt ?? undefined,
    organization_id: anyItem.organization_id ?? undefined,
    project_id: anyItem.project_id ?? undefined,
  };
};

export const LinkedDocumentSelector: React.FC<
  LinkedDocumentSelectorProps
> = ({
  letterId,
  organizationId,
  projectId,
  conversationThread,
  activeLetterCode,
  onSelectionChange,
}) => {
  const [initialLoading, setInitialLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<ContextDocumentSummary[]>(
    []
  );
  const [context, setContext] = useState<ContextDocumentsResponse>({
    document_ids: [],
    documents: [],
    suggested_documents: [],
  });

  const selectedLookup = useMemo(() => {
    const record: Record<string, ContextDocumentSummary> = {};
    for (const doc of context.documents) {
      record[doc.id] = doc;
    }
    for (const doc of context.suggested_documents ?? []) {
      if (!doc.id || record[doc.id]) continue;
      record[doc.id] = doc;
    }
    return record;
  }, [context.documents, context.suggested_documents]);

  const suggestedDocuments = useMemo(
    () => context.suggested_documents ?? [],
    [context.suggested_documents]
  );

  const conversationItems = useMemo(() => {
    if (!conversationThread || conversationThread.length === 0) {
      return [];
    }

    const normalise = (value?: string) =>
      value ? String(value).trim().toLowerCase() : undefined;

    const seen = new Set<string>();
    return conversationThread
      .map((entry) => {
        const code = entry.code ?? entry.normCode ?? undefined;
        const norm = normalise(entry.normCode ?? entry.code);
        if (norm && seen.has(norm)) {
          return null;
        }
        if (norm) {
          seen.add(norm);
        }
        return {
          id: norm ?? `${entry.subject ?? "node"}-${seen.size}`,
          code,
          normCode: norm,
          subject: entry.subject ?? code ?? "Letter",
          direction: entry.direction ?? "related",
          date: entry.date ?? entry.createdAt ?? undefined,
          project: entry.project ?? undefined,
        };
      })
      .filter(Boolean)
      .sort((a: any, b: any) => {
        const aTime = a?.date ? new Date(a.date).getTime() : Number.MAX_SAFE_INTEGER;
        const bTime = b?.date ? new Date(b.date).getTime() : Number.MAX_SAFE_INTEGER;
        return aTime - bTime;
      }) as {
      id: string;
      code?: string;
      normCode?: string;
      subject: string;
      direction: string;
      date?: string;
      project?: string;
    }[];
  }, [conversationThread]);

  const activeNormCode = useMemo(() => {
    if (!activeLetterCode) return undefined;
    return String(activeLetterCode).trim().toLowerCase();
  }, [activeLetterCode]);

  const handleSelectionChange = useCallback(
    (ids: string[], docs: ContextDocumentSummary[]) => {
      onSelectionChange?.(ids, docs);
    },
    [onSelectionChange]
  );

  const loadContextDocuments = useCallback(async () => {
    if (!letterId) return;
    setInitialLoading(true);
    try {
      const data = await fetchContextDocuments(letterId);
      const normalised: ContextDocumentsResponse = {
        document_ids: data.document_ids ?? [],
        documents: data.documents ?? [],
        suggested_documents: data.suggested_documents ?? [],
      };
      setContext(normalised);
      handleSelectionChange(normalised.document_ids, normalised.documents);
    } catch (error: any) {
      toast.error("Unable to load linked documents", {
        description: error?.message ?? "Please try again later.",
      });
    } finally {
      setInitialLoading(false);
    }
  }, [letterId, handleSelectionChange]);

  useEffect(() => {
    loadContextDocuments();
  }, [loadContextDocuments]);

  const runSearch = useCallback(
    async (search: string) => {
      if (!search || search.trim().length < 2) {
        setSearchResults([]);
        return;
      }
      setSearchLoading(true);
      try {
        const data = await listDocuments({
          q: search.trim(),
          organization_id: organizationId,
          project_id: projectId ?? undefined,
          limit: 20,
        });
        const items = Array.isArray(data?.documents) ? data.documents : [];
        setSearchResults(items.map(toSummary));
      } catch (error: any) {
        toast.error("Document search failed", {
          description: error?.message ?? "Unable to query documents.",
        });
      } finally {
        setSearchLoading(false);
      }
    },
    [organizationId, projectId]
  );

  useEffect(() => {
    if (!query) {
      setSearchResults([]);
      return;
    }
    const timeout = window.setTimeout(() => {
      runSearch(query);
    }, 400);
    return () => window.clearTimeout(timeout);
  }, [query, runSearch]);

  const persistSelection = useCallback(
    async (nextIds: string[]) => {
      setSaving(true);
      try {
        const data = await saveContextDocuments(letterId, nextIds);
        const normalised: ContextDocumentsResponse = {
          document_ids: data.document_ids ?? [],
          documents: data.documents ?? [],
          suggested_documents: data.suggested_documents ?? [],
        };
        setContext(normalised);
        handleSelectionChange(normalised.document_ids, normalised.documents);
        toast.success("Context documents updated", {
          description: `${normalised.document_ids.length} document(s) selected for LangGraph context.`,
        });
      } catch (error: any) {
        toast.error("Failed to update context documents", {
          description: error?.message ?? "Please retry in a moment.",
        });
      } finally {
        setSaving(false);
      }
    },
    [letterId, handleSelectionChange]
  );

  const toggleDocument = useCallback(
    (doc: ContextDocumentSummary) => {
      if (!doc?.id) return;
      const isSelected = context.document_ids.includes(doc.id);
      const nextIds = isSelected
        ? context.document_ids.filter((id) => id !== doc.id)
        : [...context.document_ids, doc.id];
      void persistSelection(nextIds);
    },
    [context.document_ids, persistSelection]
  );

  return (
    <Card className="border-dashed">
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <CardTitle className="text-base font-semibold">
            Document Selection
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Choose which documents inform background generation and drafting.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={loadContextDocuments}
          disabled={initialLoading}
          className="gap-2"
        >
          <RefreshCcw
            className={`h-4 w-4 ${initialLoading ? "animate-spin" : ""}`}
          />
          Refresh
        </Button>
      </CardHeader>
      <CardContent className="space-y-6">
        <section>
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium text-muted-foreground">
              Selected documents ({context.document_ids.length})
            </h4>
            {saving && (
              <Badge variant="outline" className="text-xs">
                Saving…
              </Badge>
            )}
          </div>
          <div className="mt-3 space-y-3">
            {initialLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : context.document_ids.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No documents linked yet. Use the search below to add supporting
                evidence.
              </p>
            ) : (
              context.document_ids.map((id) => {
                const doc = selectedLookup[id];
                return (
                  <div
                    key={id}
                    className="flex items-start gap-3 rounded-md border p-3"
                  >
                    <Checkbox
                      checked
                      onCheckedChange={() =>
                        doc ? toggleDocument(doc) : undefined
                      }
                      className="mt-1"
                    />
                      <div className="flex-1 space-y-1">
                        <div className="flex items-center gap-2">
                          <FileText className="h-4 w-4 text-muted-foreground" />
                          <span className="font-medium text-sm">
                            {doc?.subject ?? "Document"}
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                          {doc?.letterNo && (
                            <Badge variant="outline">Letter {doc.letterNo}</Badge>
                          )}
                          {doc?.uploadType && (
                            <Badge variant="outline">{doc.uploadType}</Badge>
                          )}
                          <span className="text-muted-foreground/80">{id}</span>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {doc?.id && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              asChild
                            >
                              <Link
                                to={`/documentviewer/${doc.id}`}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                <ExternalLink className="mr-1 h-3 w-3" />
                                Open document
                              </Link>
                            </Button>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })
            )}
          </div>
        </section>

        {suggestedDocuments.length > 0 && (
          <>
            <Separator />
            <section>
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium text-muted-foreground">
                  Suggested documents
                </h4>
                <Badge variant="outline" className="text-xs">
                  {suggestedDocuments.length}
                </Badge>
              </div>
              <div className="mt-3 space-y-2">
                {suggestedDocuments.map((doc) => {
                  const isSelected = context.document_ids.includes(doc.id);
                  return (
                    <label
                      key={`suggested-${doc.id}`}
                      className="flex cursor-pointer items-start gap-3 rounded-md border p-3 hover:bg-muted/50"
                    >
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => toggleDocument(doc)}
                        className="mt-1"
                      />
                      <div className="flex-1 space-y-1">
                        <div className="flex items-center gap-2">
                          <FileText className="h-4 w-4 text-muted-foreground" />
                          <span className="font-medium text-sm">
                            {doc.subject ?? "Document"}
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                          {doc.letterNo && (
                            <Badge variant="outline">
                              Letter {doc.letterNo}
                            </Badge>
                          )}
                          {doc.uploadType && (
                            <Badge variant="outline">{doc.uploadType}</Badge>
                          )}
                          <span>{doc.id}</span>
                        </div>
                        {doc.id && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2 text-xs"
                            asChild
                          >
                            <Link
                              to={`/documentviewer/${doc.id}`}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              <ExternalLink className="mr-1 h-3 w-3" />
                              View
                            </Link>
                          </Button>
                        )}
                      </div>
                    </label>
                  );
                })}
              </div>
            </section>
          </>
        )}

        {conversationItems.length > 0 && (
          <>
            <Separator />
            <section>
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium text-muted-foreground">
                  Conversation history
                </h4>
                <Badge variant="outline" className="text-xs">
                  {conversationItems.length}
                </Badge>
              </div>
              <div className="mt-3 space-y-2">
                {conversationItems.map((entry) => {
                  const isActive =
                    entry.normCode && entry.normCode === activeNormCode;
                  const directionLabel =
                    entry.direction === "incoming"
                      ? "Incoming"
                      : entry.direction === "outgoing"
                      ? "Outgoing"
                      : "Related";
                  return (
                    <div
                      key={entry.id}
                      className={`rounded-md border p-3 ${
                        isActive ? "border-primary/60 bg-primary/5" : ""
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <GitBranch className="h-4 w-4 text-muted-foreground" />
                          <span className="font-medium text-sm">
                            {entry.subject}
                          </span>
                        </div>
                        <Badge variant={isActive ? "default" : "secondary"}>
                          {directionLabel}
                        </Badge>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                        {entry.code && <span>Code: {entry.code}</span>}
                        {entry.project && <span>Project: {entry.project}</span>}
                        {entry.date && (
                          <span>
                            Updated: {formatDate(entry.date)}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          </>
        )}

        <Separator />

        <section>
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium text-muted-foreground">
              Search documents
            </h4>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search by subject, letter no, or filename…"
                className="pl-9"
              />
            </div>
            <Button
              variant="outline"
              onClick={() => runSearch(query)}
              disabled={searchLoading}
            >
              {searchLoading ? "Searching…" : "Search"}
            </Button>
          </div>
          <div className="mt-4 space-y-2">
            {searchLoading && (
              <p className="text-sm text-muted-foreground">Searching…</p>
            )}
            {!searchLoading && searchResults.length === 0 && query.length > 1 && (
              <p className="text-sm text-muted-foreground">
                No matching documents found.
              </p>
            )}
            {searchResults.map((doc) => {
              const isSelected = context.document_ids.includes(doc.id);
              return (
                <label
                  key={`search-${doc.id}`}
                  className="flex cursor-pointer items-start gap-3 rounded-md border p-3 hover:bg-muted/50"
                >
                  <Checkbox
                    checked={isSelected}
                    onCheckedChange={() => toggleDocument(doc)}
                    className="mt-1"
                  />
                  <div className="flex-1 space-y-1">
                    <div className="flex items-center gap-2">
                      <FileText className="h-4 w-4 text-muted-foreground" />
                      <span className="font-medium text-sm">
                        {doc.subject ?? "Document"}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                      {doc.letterNo && (
                        <Badge variant="outline">Letter {doc.letterNo}</Badge>
                      )}
                      {doc.uploadType && (
                        <Badge variant="outline">{doc.uploadType}</Badge>
                      )}
                      <span>{doc.id}</span>
                    </div>
                    {doc.id && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        asChild
                      >
                        <Link
                          to={`/documentviewer/${doc.id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <ExternalLink className="mr-1 h-3 w-3" />
                          View
                        </Link>
                      </Button>
                    )}
                  </div>
                </label>
              );
            })}
          </div>
        </section>
      </CardContent>
    </Card>
  );
};

export default LinkedDocumentSelector;
