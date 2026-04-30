import React, { useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertTriangle,
  BookOpen,
  ExternalLink,
  FileText,
  MessageSquare,
} from "lucide-react";
import type {
  LanggraphDraftReviewFinding,
  LanggraphDraftSource,
} from "@/types/langgraph";

interface DraftSourcesPanelProps {
  sources?: LanggraphDraftSource[];
  reviewerFindings?: LanggraphDraftReviewFinding[];
}

const buildContractSearchLink = (source: LanggraphDraftSource) => {
  const params = new URLSearchParams();
  const uploadId =
    (source.metadata?.upload_id as string | undefined) ??
    source.document_id ??
    undefined;
  const query = source.clause_number
    ? `Clause ${source.clause_number}`
    : source.label;
  if (query) {
    params.set("query", query);
  }
  if (uploadId) {
    params.set("document_id", uploadId);
  }
  const queryString = params.toString();
  return queryString ? `/contracts/search?${queryString}` : "/contracts/search";
};

const iconForSource = (sourceType?: string) => {
  switch (sourceType) {
    case "contract_clause":
      return <BookOpen className="mt-0.5 h-4 w-4 text-amber-600" />;
    case "letter":
      return <FileText className="mt-0.5 h-4 w-4 text-blue-600" />;
    case "comment":
      return <MessageSquare className="mt-0.5 h-4 w-4 text-purple-600" />;
    default:
      return <FileText className="mt-0.5 h-4 w-4 text-muted-foreground" />;
  }
};

const DraftSourcesPanel: React.FC<DraftSourcesPanelProps> = ({
  sources = [],
  reviewerFindings = [],
}) => {
  const groupedSources = useMemo(() => {
    const grouped: Record<string, LanggraphDraftSource[]> = {
      contract_clause: [],
      letter: [],
      context_document: [],
      comment: [],
      other: [],
    };
    sources.forEach((source) => {
      const bucket = grouped[source.source_type] ? source.source_type : "other";
      grouped[bucket].push(source);
    });
    return grouped;
  }, [sources]);

  const hasSources = sources.length > 0;

  return (
    <Card className="border-dashed">
      <CardHeader>
        <CardTitle className="text-base font-semibold">
          Draft Sources
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {reviewerFindings.length > 0 && (
          <section className="rounded-md border border-amber-200 bg-amber-50 p-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-amber-900">
              <AlertTriangle className="h-4 w-4" />
              Reviewer checks
            </div>
            <ul className="mt-2 space-y-2 text-sm text-amber-900/80">
              {reviewerFindings.map((finding, index) => (
                <li key={`${finding.message}-${index}`}>
                  <span className="font-semibold uppercase">
                    {finding.level}
                  </span>
                  : {finding.message}
                  {finding.evidence ? ` (${finding.evidence})` : ""}
                </li>
              ))}
            </ul>
          </section>
        )}

        {hasSources ? (
          Object.entries(groupedSources).map(([group, items]) => {
            if (!items.length) return null;
            const title =
              group === "contract_clause"
                ? "Contract clauses"
                : group === "letter"
                ? "Correspondence"
                : group === "context_document"
                ? "Context documents"
                : group === "comment"
                ? "Comments"
                : "Other sources";
            return (
              <section key={group} className="space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-medium text-muted-foreground">
                    {title}
                  </h4>
                  <Badge variant="outline" className="text-xs">
                    {items.length}
                  </Badge>
                </div>
                <div className="space-y-2">
                  {items.map((source) => {
                    const link = source.document_id
                      ? `/documentviewer/${source.document_id}`
                      : source.letter_id
                      ? `/letters/summary/${source.letter_id}`
                      : source.source_type === "contract_clause"
                      ? buildContractSearchLink(source)
                      : undefined;
                    return (
                      <div
                        key={source.id}
                        className="rounded-md border border-border/60 bg-muted/30 p-3"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="space-y-1">
                            <div className="flex items-start gap-2">
                              {iconForSource(source.source_type)}
                              <div>
                                <p className="text-sm font-medium">
                                  {source.label}
                                </p>
                                <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                  {source.clause_number && (
                                    <Badge variant="outline">
                                      Clause {source.clause_number}
                                    </Badge>
                                  )}
                                  {source.clause_title && (
                                    <Badge variant="outline">
                                      {source.clause_title}
                                    </Badge>
                                  )}
                                  {source.page_numbers &&
                                    source.page_numbers.length > 0 && (
                                      <Badge variant="outline">
                                        Pages {source.page_numbers.join(", ")}
                                      </Badge>
                                    )}
                                  {typeof source.score === "number" && (
                                    <Badge variant="outline">
                                      Score {source.score.toFixed(2)}
                                    </Badge>
                                  )}
                                </div>
                              </div>
                            </div>
                            {source.snippet && (
                              <p className="text-sm text-muted-foreground">
                                {source.snippet}
                              </p>
                            )}
                          </div>
                          {link && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              asChild
                            >
                              <a
                                href={link}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                <ExternalLink className="mr-1 h-3 w-3" />
                                View
                              </a>
                            </Button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
            );
          })
        ) : (
          <p className="text-sm text-muted-foreground">
            Sources will appear after LangGraph runs with retrieval enabled.
          </p>
        )}
      </CardContent>
    </Card>
  );
};

export default DraftSourcesPanel;
