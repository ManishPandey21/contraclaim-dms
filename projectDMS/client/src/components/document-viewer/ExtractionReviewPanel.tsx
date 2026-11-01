import React, { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Eye, Loader2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { scopeRefusalMessage } from "@/services/active-scope";
import {
  decideExtractionPage,
  getExtractionReview,
  type ExtractionReview,
  type ExtractionReviewPage,
} from "@/services/extraction-review-api";

interface ExtractionReviewPanelProps {
  documentId: string;
  /** The PDF page currently shown beside the panel. */
  currentPage?: number | null;
  /** Show this page of the source PDF. */
  onShowPage: (page: number) => void;
  /** A decision changed the document's state; reload status and record. */
  onDecided: () => void;
}

function errorMessage(error: unknown): string {
  const scope = scopeRefusalMessage(error, "document");
  if (scope) return scope;
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message?: unknown }).message);
  }
  if (typeof detail === "string") return detail;
  return error instanceof Error ? error.message : "The review could not be saved";
}

const STATE_LABELS: Record<string, string> = {
  required: "Human review required",
  publication_pending: "Review resolved; publication pending",
  resolved: "Review resolved",
  not_required: "No extraction review pending",
};

const PageCard: React.FC<{
  page: ExtractionReviewPage;
  review: ExtractionReview;
  shown: boolean;
  busy: boolean;
  onShowPage: (page: number) => void;
  onDecide: (page: ExtractionReviewPage, action: "approve" | "reject", comment: string) => void;
}> = ({ page, review, shown, busy, onShowPage, onDecide }) => {
  const [comment, setComment] = useState("");
  const [inspected, setInspected] = useState(false);
  const decision = page.decision;
  const editable = review.reviewable && review.can_review;
  const approved = decision?.action === "approved_as_is";

  useEffect(() => {
    if (shown) setInspected(true);
  }, [shown]);

  return (
    <section
      className="space-y-3 rounded-md border bg-white p-3"
      aria-label={`Review page ${page.page_number}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Page {page.page_number}</h3>
        <Button
          size="sm"
          variant={shown ? "secondary" : "outline"}
          onClick={() => onShowPage(page.page_number)}
        >
          <Eye className="mr-1 h-4 w-4" />
          {shown ? "Shown in viewer" : "Show source page"}
        </Button>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Extraction method</dt>
        <dd>{page.source || "unknown"}{page.status ? ` (${page.status})` : ""}</dd>
        <dt className="text-muted-foreground">Machine verdict</dt>
        <dd>
          <Badge variant="danger">{page.machine_quality_verdict || "not recorded"}</Badge>
        </dd>
        <dt className="text-muted-foreground">Tables found</dt>
        <dd>{page.table_count}</dd>
      </dl>

      {page.machine_failed_checks.length > 0 && (
        <div className="text-xs">
          <p className="font-medium">Why the quality check flagged it</p>
          <ul className="ml-4 list-disc">
            {page.machine_failed_checks.map((check, index) => (
              <li key={`${check.name}-${index}`}>
                {check.name}
                {check.detail ? `: ${check.detail}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {page.unresolved && (
        <p className="text-xs text-red-700">
          No text was extracted from this page{page.error ? ` (${page.error})` : ""}. It cannot be
          approved as-is; it needs re-extraction.
        </p>
      )}

      <div>
        <p className="mb-1 text-xs font-medium">Extracted text ({page.char_count} characters)</p>
        <pre
          className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs"
          aria-label={`Extracted text of page ${page.page_number}`}
        >
          {page.text || "(no text)"}
        </pre>
      </div>

      {page.tables.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer font-medium">Extracted tables ({page.tables.length})</summary>
          {page.tables.map((table, tableIndex) => (
            <table key={tableIndex} className="mt-2 w-full border-collapse">
              <tbody>
                {table.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex} className="border px-1 py-0.5 align-top">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
        </details>
      )}

      {decision && (
        <p className={`text-xs ${approved ? "text-green-700" : "text-red-700"}`}>
          {approved ? "Approved as-is" : "Rejected"} by {decision.reviewed_by_label || decision.reviewed_by}
          {decision.reviewed_at ? ` on ${new Date(decision.reviewed_at).toLocaleString()}` : ""}
          {decision.comment ? ` - "${decision.comment}"` : ""}
        </p>
      )}

      {editable && !approved && (
        <div className="space-y-2">
          <Textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Review comment (required to reject)"
            aria-label={`Review comment for page ${page.page_number}`}
            maxLength={2000}
          />
          {!inspected && (
            <p className="text-xs text-muted-foreground">
              Open the source page in the viewer before deciding.
            </p>
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              onClick={() => onDecide(page, "approve", comment)}
              disabled={busy || !inspected || !page.approvable}
            >
              {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-1 h-4 w-4" />}
              Approve as-is
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => onDecide(page, "reject", comment)}
              disabled={busy || !inspected || !comment.trim()}
            >
              <XCircle className="mr-1 h-4 w-4" />
              Reject
            </Button>
          </div>
        </div>
      )}
    </section>
  );
};

const ExtractionReviewPanel: React.FC<ExtractionReviewPanelProps> = ({
  documentId,
  currentPage,
  onShowPage,
  onDecided,
}) => {
  const [review, setReview] = useState<ExtractionReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyPage, setBusyPage] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setReview(await getExtractionReview(documentId));
    } catch (error) {
      setLoadError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = useCallback(
    async (page: ExtractionReviewPage, action: "approve" | "reject", comment: string) => {
      if (!review) return;
      setBusyPage(page.page_number);
      try {
        const result = await decideExtractionPage(documentId, page, action, review, comment);
        if (action === "reject") {
          toast.success(`Page ${page.page_number} rejected; the document stays in review`);
        } else if (result.resolution.resolved) {
          toast.success("All review pages approved; the document is being published");
        } else {
          toast.success(`Page ${page.page_number} approved`, {
            description: result.resolution.unresolved_pages?.length
              ? `Still to review: page(s) ${result.resolution.unresolved_pages.join(", ")}`
              : undefined,
          });
        }
        await load();
        onDecided();
      } catch (error) {
        toast.error("Review not saved", { description: errorMessage(error) });
        await load();
      } finally {
        setBusyPage(null);
      }
    },
    [documentId, load, onDecided, review]
  );

  if (loading && !review) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading extraction review...
      </div>
    );
  }
  if (loadError) {
    return <p className="text-sm text-red-700">{loadError}</p>;
  }
  if (!review) return null;

  return (
    <div className="space-y-3" data-testid="extraction-review-panel">
      <div className="text-sm">
        <p className="font-medium">{STATE_LABELS[review.review_state] || review.review_state}</p>
        {review.review_state === "required" && (
          <p className="text-xs text-muted-foreground">
            The automated quality check could not verify these pages. Compare each with the source
            page and approve the extraction only if it is acceptable. The machine verdict stays on
            record either way.
          </p>
        )}
        {review.reviewable && !review.can_review && (
          <p className="text-xs text-amber-800">You can view this review but not decide it.</p>
        )}
        {review.canonical_revision != null && (
          <p className="text-xs text-muted-foreground">Evidence revision {review.canonical_revision}</p>
        )}
      </div>
      {review.pages.map((page) => (
        <PageCard
          key={`${review.canonical_revision}-${page.page_number}`}
          page={page}
          review={review}
          shown={currentPage === page.page_number}
          busy={busyPage === page.page_number}
          onShowPage={onShowPage}
          onDecide={(p, action, comment) => void decide(p, action, comment)}
        />
      ))}
    </div>
  );
};

export default ExtractionReviewPanel;
