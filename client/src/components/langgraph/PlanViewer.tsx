import React, { useMemo, useState } from "react";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { MessageSquare, ExternalLink, Eye, EyeOff } from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  LanggraphContextDocument,
  LanggraphNodeTrace,
} from "@/types/langgraph";
import type { ContextDocumentSummary } from "@/services/letter-workflow-api";

type DocumentSummarySource =
  | LanggraphContextDocument
  | ContextDocumentSummary
  | Record<string, unknown>;

interface PlanViewerProps {
  plan?: string;
  summaryPoints?: string[];
  trace?: LanggraphNodeTrace[];
  documents?: DocumentSummarySource[];
  className?: string;
}

interface PlanSection {
  id: string;
  title: string;
  body: string;
}

interface CitationIndexEntry {
  id: string;
  label: string;
  letterNo?: string;
  subject?: string;
  url?: string;
  keywords: string[];
}

const extractSections = (plan?: string): PlanSection[] => {
  if (!plan) return [];
  const normalised = plan.replace(/\r\n/g, "\n").trim();
  if (!normalised) return [];

  const blocks = normalised
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter((block) => block.length > 0);

  return blocks.map((block, index) => {
    const [firstLine, ...rest] = block.split("\n");
    const heading = firstLine.replace(/^[#*\d.\-\s]+/, "").trim();
    const title =
      heading.length > 0 ? heading.slice(0, 80) : `Section ${index + 1}`;
    const body = [firstLine, ...rest].join("\n").trim();
    return {
      id: `plan-section-${index}`,
      title,
      body,
    };
  });
};

const normaliseDocument = (doc: DocumentSummarySource): CitationIndexEntry | null => {
  if (!doc) return null;
  const payload = doc as Record<string, any>;
  const rawId =
    payload.id ??
    payload.documentId ??
    payload.document_id ??
    payload._id ??
    payload.upload_id ??
    undefined;
  if (!rawId) return null;
  const subjectSource =
    payload.subject ??
    payload.title ??
    payload.name ??
    payload.filename ??
    `Document ${String(rawId).slice(-6)}`;
  const letterNo =
    payload.letterNo ??
    payload.letter_no ??
    payload.referenceNumber ??
    payload.reference_number ??
    undefined;

  const subject =
    typeof subjectSource === "string"
      ? subjectSource
      : String(subjectSource ?? rawId);
  const label = letterNo ? `${subject} (${letterNo})` : subject;

  const keywords = new Set<string>();
  if (typeof letterNo === "string" && letterNo.trim().length > 0) {
    keywords.add(letterNo.trim().toLowerCase());
  }
  const subjectToken = subject.trim();
  if (subjectToken.length > 0 && subjectToken.length <= 120) {
    keywords.add(subjectToken.toLowerCase());
  }
  keywords.add(String(rawId).toLowerCase());

  return {
    id: String(rawId),
    label,
    letterNo: letterNo ? String(letterNo) : undefined,
    subject,
    url: `/documentviewer/${rawId}`,
    keywords: Array.from(keywords).filter((value) => value.length > 2),
  };
};

export const PlanViewer: React.FC<PlanViewerProps> = ({
  plan,
  summaryPoints,
  trace,
  documents,
  className,
}) => {
  const [showDetails, setShowDetails] = useState(true);

  const sections = useMemo(() => extractSections(plan), [plan]);

  const citationIndex = useMemo(() => {
    const index = new Map<string, CitationIndexEntry>();
    for (const doc of documents ?? []) {
      const entry = normaliseDocument(doc);
      if (entry) {
        index.set(entry.id, entry);
      }
    }
    return index;
  }, [documents]);

  const enrichedSections = useMemo(() => {
    if (sections.length === 0) {
      return [];
    }
    const collection = Array.from(citationIndex.values());
    return sections.map((section) => {
      const lowerBody = section.body.toLowerCase();
      const citations = collection.filter((entry) =>
        entry.keywords.some((keyword) => lowerBody.includes(keyword))
      );
      return {
        ...section,
        citations,
      };
    });
  }, [sections, citationIndex]);

  const { sectionAnnotations, remainingSummary } = useMemo(() => {
    if (!summaryPoints || summaryPoints.length === 0) {
      return {
        sectionAnnotations: new Map<string, string[]>(),
        remainingSummary: [] as string[],
      };
    }

    const annotations = new Map<string, string[]>();
    const unused = new Set(summaryPoints);
    const preparedSections =
      sections.length > 0 ? sections : [{ id: "full-plan", body: plan ?? "" }];

    preparedSections.forEach((section) => {
      const lower = section.body.toLowerCase();
      const matches = summaryPoints.filter((point) =>
        lower.includes(point.toLowerCase())
      );
      if (matches.length > 0) {
        annotations.set(section.id, matches);
        matches.forEach((match) => unused.delete(match));
      }
    });

    return {
      sectionAnnotations: annotations,
      remainingSummary: Array.from(unused),
    };
  }, [plan, sections, summaryPoints]);

  return (
    <div className={cn("space-y-4", className)}>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-muted-foreground">
          Strategic Plan Overview
        </h3>
        {plan && (
          <Button
            variant="ghost"
            size="sm"
            className="gap-2"
            onClick={() => setShowDetails((previous) => !previous)}
          >
            {showDetails ? (
              <>
                <EyeOff className="h-4 w-4" />
                Hide details
              </>
            ) : (
              <>
                <Eye className="h-4 w-4" />
                Show details
              </>
            )}
          </Button>
        )}
      </div>

      {plan ? (
        showDetails ? (
          enrichedSections.length > 0 ? (
            <Accordion
              type="multiple"
              defaultValue={enrichedSections.map((section) => section.id)}
              className="overflow-hidden rounded-md border bg-muted/30"
            >
              {enrichedSections.map((section) => (
                <AccordionItem key={section.id} value={section.id}>
                  <AccordionTrigger className="px-4 text-left text-sm font-medium">
                    <div className="flex flex-col gap-1 text-left">
                      <span>{section.title}</span>
                      {section.citations.length > 0 && (
                        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <span className="font-semibold uppercase tracking-wide">
                            Citations
                          </span>
                          {section.citations.map((citation) => (
                            <Button
                              key={`${section.id}-${citation.id}`}
                              variant="outline"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              asChild
                            >
                              <a
                                href={citation.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1"
                              >
                                <ExternalLink className="h-3 w-3" />
                                {citation.label}
                              </a>
                            </Button>
                          ))}
                        </div>
                      )}
                    </div>
                  </AccordionTrigger>
                  <AccordionContent className="px-4 pb-4">
                    <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                      {section.body}
                    </p>
                    {sectionAnnotations.get(section.id)?.length ? (
                      <div className="mt-3 space-y-2">
                        <Separator className="my-2" />
                        {sectionAnnotations.get(section.id)!.map((point) => (
                          <div
                            key={`${section.id}-${point}`}
                            className="flex items-start gap-2 text-sm text-muted-foreground"
                          >
                            <MessageSquare className="mt-0.5 h-4 w-4 text-amber-500" />
                            <span>{point}</span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          ) : (
            <pre className="whitespace-pre-wrap rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">
              {plan}
            </pre>
          )
        ) : (
          <pre className="whitespace-pre-wrap rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">
            {plan}
          </pre>
        )
      ) : (
        <p className="text-sm text-muted-foreground">
          No strategic plan available yet. Generate a background summary to begin.
        </p>
      )}

      {remainingSummary.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-muted-foreground">
            Key themes
          </h4>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
            {remainingSummary.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
      )}

      {trace && trace.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-muted-foreground">
            Execution timeline
          </h4>
          <div className="mt-2 space-y-2">
            {trace.map((node) => (
              <details
                key={`${node.name}-${node.started_at}`}
                className="rounded-md border bg-background p-3"
              >
                <summary className="cursor-pointer text-sm font-medium">
                  {node.name} · {node.status}
                </summary>
                <Separator className="my-2" />
                <dl className="space-y-2 text-xs text-muted-foreground">
                  <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-2">
                    <dt className="font-semibold uppercase tracking-wide">
                      Started
                    </dt>
                    <dd>{node.started_at}</dd>
                  </div>
                  <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-2">
                    <dt className="font-semibold uppercase tracking-wide">
                      Completed
                    </dt>
                    <dd>{node.completed_at}</dd>
                  </div>
                  {Object.keys(node.data || {}).length > 0 && (
                    <div>
                      <dt className="font-semibold uppercase tracking-wide">
                        Data
                      </dt>
                      <dd>
                        <pre className="mt-1 whitespace-pre-wrap rounded-md bg-muted/40 p-2">
                          {JSON.stringify(node.data, null, 2)}
                        </pre>
                      </dd>
                    </div>
                  )}
                </dl>
              </details>
            ))}
          </div>
        </section>
      )}
    </div>
  );
};

export default PlanViewer;
