import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Copy, FileSearch, RefreshCw, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  DETAILED_SECTIONS,
  fetchGroundedSummary,
  generateGroundedSummary,
  sectionItems,
  type GroundedSummaryItem,
  type GroundedSummaryView,
  type SummaryStatus,
  type VerificationStatus,
} from "@/services/groundedSummary";

const STATUS_BADGE: Record<VerificationStatus, { label: string; variant: BadgeProps["variant"] }> = {
  verified: { label: "Verified", variant: "success" },
  source_stated: { label: "As stated", variant: "neutral" },
  human_approved: { label: "Human-approved", variant: "primary" },
  not_checkable: { label: "Not checkable", variant: "outline" },
  uncertain: { label: "Uncertain", variant: "warning" },
};

/** Why no summary is shown, in the user's words. */
const STATUS_MESSAGE: Partial<Record<SummaryStatus, string>> = {
  not_generated: "No grounded summary has been generated for this letter yet.",
  generating: "The summary is being regenerated for the current version of this letter.",
  stale:
    "The letter's extracted text has changed since the summary was generated. The old summary is hidden until it is regenerated.",
  blocked_for_review:
    "This letter is awaiting extraction review. Its summary is held until a reviewer releases the pages.",
  withheld: "This letter is quarantined or removed; no summary is shown.",
  unavailable:
    "This letter was processed before page-level evidence existed, so a grounded summary cannot be built. The AI key points below still apply.",
  failed: "The summary could not be generated. The letter itself is unaffected.",
};

const REGENERATABLE: ReadonlySet<SummaryStatus> = new Set(["not_generated", "stale", "failed"]);

function PageLinks({ documentId, pages }: { documentId: string; pages: number[] }) {
  if (!pages.length) return null;
  return (
    <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
      {pages.map((page) => (
        <Link
          key={page}
          to={`/documentviewer/${documentId}?page=${page}`}
          className="text-xs font-medium text-blue-700 underline-offset-2 hover:underline"
          aria-label={`Open page ${page} of the source document`}
        >
          p.{page}
        </Link>
      ))}
    </span>
  );
}

function ItemRow({ item, documentId }: { item: GroundedSummaryItem; documentId: string }) {
  const badge = STATUS_BADGE[item.verification_status] ?? STATUS_BADGE.uncertain;
  return (
    <li className="text-sm leading-relaxed break-words">
      <span>{item.text}</span>
      <PageLinks documentId={documentId} pages={item.page_numbers} />
      <Badge variant={badge.variant} className="ml-2 align-middle text-[11px] px-1.5 py-0">
        {badge.label}
      </Badge>
    </li>
  );
}

function copyableText(view: GroundedSummaryView): string {
  const parts: string[] = [];
  if (view.short_summary) parts.push(view.short_summary, "");
  if (view.detailed_summary?.markdown) parts.push(view.detailed_summary.markdown);
  return parts.join("\n").trim();
}

export interface GroundedSummaryPanelProps {
  documentId: string;
  canGenerate: boolean;
}

const GroundedSummaryPanel: React.FC<GroundedSummaryPanelProps> = ({ documentId, canGenerate }) => {
  const [view, setView] = useState<GroundedSummaryView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setView(await fetchGroundedSummary(documentId));
    } catch (err: any) {
      setError(err?.message || "Failed to load the summary");
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    load();
  }, [load]);

  const regenerate = async () => {
    setGenerating(true);
    try {
      const next = await generateGroundedSummary(documentId);
      setView(next);
      if (next.detailed_summary) toast.success("Summary generated from the current letter text");
      else toast.error(STATUS_MESSAGE[next.summary_status] || "Summary not generated");
    } catch (err: any) {
      toast.error(err?.message || "Failed to generate the summary");
    } finally {
      setGenerating(false);
    }
  };

  const copySummary = async () => {
    if (!view) return;
    try {
      await navigator.clipboard.writeText(copyableText(view));
      toast.success("Summary copied");
    } catch {
      toast.error("Could not copy the summary");
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">Loading summary…</CardContent>
      </Card>
    );
  }

  if (error || !view) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-red-600">{error || "Summary unavailable"}</CardContent>
      </Card>
    );
  }

  const detailed = view.detailed_summary;
  const shown = Boolean(detailed);
  const message = STATUS_MESSAGE[view.summary_status];
  const sections = detailed
    ? DETAILED_SECTIONS.map(([key, title]) => ({ key, title, items: sectionItems(detailed, key) })).filter(
        (section) => section.items.length
      )
    : [];
  const reviewed = detailed?.generated_from?.review_resolution;

  return (
    <div className="space-y-4" data-testid="grounded-summary">
      <Card>
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle className="text-lg flex items-center gap-2">
              <ShieldCheck className="h-4 w-4" /> Short Summary
            </CardTitle>
            <CardDescription>
              Built only from facts located in the letter's pages. The letter itself remains the source.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            {shown && (
              <Button size="sm" variant="outline" onClick={copySummary}>
                <Copy className="h-4 w-4 mr-2" /> Copy Summary
              </Button>
            )}
            {canGenerate && REGENERATABLE.has(view.summary_status) && (
              <Button size="sm" variant="outline" onClick={regenerate} disabled={generating}>
                <RefreshCw className={`h-4 w-4 mr-2 ${generating ? "animate-spin" : ""}`} />
                {view.summary_status === "not_generated" ? "Generate" : "Regenerate"}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {view.short_summary && <p className="text-base font-medium leading-relaxed">{view.short_summary}</p>}
          {!shown && message && (
            <div
              role="status"
              className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{message}</span>
            </div>
          )}
          {view.summary_status === "degraded" && (
            <div role="status" className="text-xs text-amber-800 dark:text-amber-200">
              Parts of this summary are qualified — see Uncertainty / Review below.
            </div>
          )}
        </CardContent>
      </Card>

      {detailed && (
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="text-lg flex items-center gap-2">
                <FileSearch className="h-4 w-4" /> Detailed Summary
              </CardTitle>
              <CardDescription>
                Each point cites the page it was read from. Statements are the author's, attributed and quoted — not
                findings. Revision {detailed.generated_from.canonical_revision}
                {reviewed ? " · released by human review" : ""}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-5">
            {detailed.executive_overview && (
              <section aria-labelledby="gs-overview">
                <h3 id="gs-overview" className="text-sm font-semibold mb-1">
                  Overview
                </h3>
                <p className="text-sm leading-relaxed">{detailed.executive_overview}</p>
              </section>
            )}
            {sections.map((section) => (
              <section key={section.key} aria-labelledby={`gs-${section.key}`}>
                <h3
                  id={`gs-${section.key}`}
                  className={`text-sm font-semibold mb-1 ${
                    section.key === "uncertainties" ? "text-amber-800 dark:text-amber-200" : ""
                  }`}
                >
                  {section.title}
                </h3>
                <ul className="list-disc pl-5 space-y-1.5">
                  {section.items.map((item, index) => (
                    <ItemRow key={`${section.key}-${index}`} item={item} documentId={documentId} />
                  ))}
                </ul>
              </section>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default GroundedSummaryPanel;
