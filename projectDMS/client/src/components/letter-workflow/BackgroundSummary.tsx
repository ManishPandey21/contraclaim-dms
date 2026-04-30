import React, { useMemo, useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  ChevronDown,
  ChevronUp,
  BookOpen,
  FileText,
  Mail,
  MessageSquare,
  StickyNote,
} from "lucide-react";
import { formatDate } from "@/utils/dateFormat";
import type {
  LanggraphBackgroundItem,
  LanggraphContextDocument,
} from "@/types/langgraph";
import type { ContextDocumentSummary } from "@/services/letter-workflow-api";

type DocumentSummarySource =
  | LanggraphContextDocument
  | ContextDocumentSummary
  | Record<string, unknown>;

interface NormalisedDocumentInfo {
  id: string;
  subject: string;
  letterNo?: string;
  uploadType?: string;
  date?: string;
  summary?: string;
  keywords?: string[];
}

export interface BackgroundSummaryProps {
  entries?: LanggraphBackgroundItem[];
  documents?: DocumentSummarySource[];
  summaryPoints?: string[];
  loading?: boolean;
  actions?: React.ReactNode;
  collapsed?: boolean;
  highlightOutdated?: boolean;
  lastGeneratedLabel?: string;
}

const mergeDocuments = (
  current: NormalisedDocumentInfo | undefined,
  next: NormalisedDocumentInfo
): NormalisedDocumentInfo => {
  if (!current) {
    return next;
  }

  const mergedKeywords = new Set<string>();
  for (const source of [current.keywords, next.keywords]) {
    if (!source) continue;
    source.forEach((keyword) => mergedKeywords.add(keyword));
  }

  return {
    id: current.id,
    subject: next.subject || current.subject,
    letterNo: next.letterNo || current.letterNo,
    uploadType: next.uploadType || current.uploadType,
    date: next.date || current.date,
    summary: next.summary || current.summary,
    keywords: mergedKeywords.size ? Array.from(mergedKeywords) : current.keywords,
  };
};

const normaliseDocument = (
  doc: DocumentSummarySource | undefined
): NormalisedDocumentInfo | null => {
  if (!doc) return null;
  const payload = doc as Record<string, unknown>;
  const rawId =
    payload.id ??
    payload.documentId ??
    payload.document_id ??
    payload._id ??
    payload.upload_id;
  if (!rawId) {
    return null;
  }

  const subject =
    (payload.subject as string) ??
    (payload.title as string) ??
    (payload.filename as string) ??
    (payload.name as string) ??
    String(rawId);

  const keywordsRaw = payload.keywords;
  const keywords =
    Array.isArray(keywordsRaw) && keywordsRaw.length
      ? keywordsRaw.map((item) => String(item))
      : undefined;

  const letterNo =
    (payload.letterNo as string) ??
    (payload.letter_no as string) ??
    (payload.referenceNumber as string) ??
    undefined;

  const uploadType =
    (payload.uploadType as string) ??
    (payload.upload_type as string) ??
    undefined;

  const date =
    (payload.date as string) ??
    (payload.createdAt as string) ??
    (payload.created_at as string) ??
    undefined;

  const summary =
    (payload.summary as string) ??
    (payload.description as string) ??
    undefined;

  return {
    id: String(rawId),
    subject,
    letterNo,
    uploadType,
    date,
    summary,
    keywords,
  };
};

const iconForEntry = (type?: string) => {
  switch (type) {
    case "comment":
      return <MessageSquare className="mt-0.5 h-4 w-4 text-blue-500" />;
    case "clause":
      return <BookOpen className="mt-0.5 h-4 w-4 text-amber-600" />;
    case "letter":
      return <Mail className="mt-0.5 h-4 w-4 text-blue-600" />;
    case "document":
    default:
      return <FileText className="mt-0.5 h-4 w-4 text-muted-foreground" />;
  }
};

const BackgroundSummary: React.FC<BackgroundSummaryProps> = ({
  entries,
  documents,
  summaryPoints,
  loading,
  actions,
  collapsed = false,
  highlightOutdated = false,
  lastGeneratedLabel,
}) => {
  const [expanded, setExpanded] = useState(!collapsed);

  const { normalisedDocs, docEntryMap, generalEntries } = useMemo(() => {
    const docMap = new Map<string, NormalisedDocumentInfo>();

    for (const doc of documents ?? []) {
      const normalised = normaliseDocument(doc);
      if (!normalised) continue;

      const existing = docMap.get(normalised.id);
      docMap.set(normalised.id, mergeDocuments(existing, normalised));
    }

    const docEntries = new Map<string, LanggraphBackgroundItem[]>();
    const general: LanggraphBackgroundItem[] = [];

    for (const entry of entries ?? []) {
      if (!entry) continue;

      if (entry.type === "summary") {
        // Summary points are rendered separately.
        continue;
      }

      const docIds = Array.isArray(entry.documents)
        ? entry.documents.filter(Boolean).map((id) => String(id))
        : [];

      if (!docIds.length) {
        general.push(entry);
        continue;
      }

      for (const docId of docIds) {
        if (!docMap.has(docId)) {
          docMap.set(docId, {
            id: docId,
            subject: `Document ${docId}`,
          });
        }
        const list = docEntries.get(docId) ?? [];
        list.push(entry);
        docEntries.set(docId, list);
      }
    }

    return {
      normalisedDocs: Array.from(docMap.values()),
      docEntryMap: docEntries,
      generalEntries: general,
    };
  }, [documents, entries]);

  const hasContent =
    (summaryPoints && summaryPoints.length > 0) ||
    normalisedDocs.length > 0 ||
    generalEntries.length > 0;

  return (
    <Card className="border-dashed">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle className="text-base font-semibold">
            AI Background Summary
          </CardTitle>
          <CardDescription>
            Review the evidence and insights collected for this letter.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          {highlightOutdated && (
            <Badge variant="destructive" className="text-xs">
              Needs refresh
            </Badge>
          )}
          {lastGeneratedLabel && (
            <Badge variant="outline" className="text-xs">
              {lastGeneratedLabel}
            </Badge>
          )}
          {actions}
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setExpanded((prev) => !prev)}
            aria-label={expanded ? "Collapse background summary" : "Expand background summary"}
          >
            {expanded ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </Button>
        </div>
      </CardHeader>

      {expanded && (
        <CardContent className="space-y-4">
          {loading ? (
            <p className="text-sm text-muted-foreground">
              Generating background�?�
            </p>
          ) : hasContent ? (
            <>
              {summaryPoints && summaryPoints.length > 0 && (
                <section>
                  <h4 className="text-sm font-medium text-muted-foreground">
                    Key themes
                  </h4>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                    {summaryPoints.map((point, index) => (
                      <li key={`${point}-${index}`}>{point}</li>
                    ))}
                  </ul>
                </section>
              )}

              {summaryPoints && summaryPoints.length > 0 && (
                <Separator className="my-2" />
              )}

              {normalisedDocs.map((doc) => {
                const entriesForDoc = docEntryMap.get(doc.id) ?? [];
                return (
                  <div
                    key={doc.id}
                    className="rounded-md border border-border/60 bg-muted/30 p-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <p className="text-sm font-medium">{doc.subject}</p>
                        <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                          {doc.letterNo && (
                            <Badge variant="outline">
                              Letter {doc.letterNo}
                            </Badge>
                          )}
                          {doc.uploadType && (
                            <Badge variant="outline">{doc.uploadType}</Badge>
                          )}
                          {doc.date && (
                            <span>{formatDate(doc.date)}</span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="mt-3 space-y-2">
                      {doc.summary && (
                        <div className="flex items-start gap-2 text-sm text-muted-foreground">
                          <StickyNote className="mt-0.5 h-4 w-4 text-amber-500" />
                          <span>{doc.summary}</span>
                        </div>
                      )}
                      {entriesForDoc.map((entry) => (
                        <div
                          key={entry.id}
                          className="flex items-start gap-2 text-sm"
                        >
                          {iconForEntry(entry.type)}
                          <span>{entry.text}</span>
                        </div>
                      ))}
                      {doc.keywords && doc.keywords.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {doc.keywords.slice(0, 6).map((keyword) => (
                            <Badge
                              key={`${doc.id}-${keyword}`}
                              variant="outline"
                              className="text-xs"
                            >
                              {keyword}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}

              {generalEntries.length > 0 && (
                <>
                  <Separator className="my-2" />
                  <section>
                    <h4 className="text-sm font-medium text-muted-foreground">
                      Additional context
                    </h4>
                    <ul className="mt-2 space-y-2 text-sm text-muted-foreground">
                      {generalEntries.map((entry) => (
                        <li key={entry.id} className="flex items-start gap-2">
                          {iconForEntry(entry.type)}
                          <span>{entry.text}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                </>
              )}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              Background summary will appear here after generation.
            </p>
          )}
        </CardContent>
      )}
    </Card>
  );
};

export default BackgroundSummary;
