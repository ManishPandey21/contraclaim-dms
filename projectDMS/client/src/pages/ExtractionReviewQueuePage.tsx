import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { scopeRefusalMessage } from "@/services/active-scope";
import {
  listExtractionReviewQueue,
  type ExtractionReviewQueueItem,
} from "@/services/extraction-review-api";

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString();
}

/**
 * Documents the extraction quality gate sent to a person (Release 1.1.1).
 * Opening a row lands on the viewer's Review tab at the first flagged page.
 */
const ExtractionReviewQueuePage: React.FC = () => {
  const navigate = useNavigate();
  const [items, setItems] = useState<ExtractionReviewQueueItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const queue = await listExtractionReviewQueue({ limit: 100 });
      setItems(queue.items);
      setTotal(queue.total);
    } catch (err) {
      setError(
        scopeRefusalMessage(err, "review queue") ||
          (err instanceof Error ? err.message : "The review queue could not be loaded")
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const open = (item: ExtractionReviewQueueItem) => {
    const page = item.first_review_page ? `&page=${item.first_review_page}` : "";
    navigate(`/documentviewer/${item.document_id}?tab=review${page}`);
  };

  return (
    <div className="space-y-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Documents needing extraction review</h1>
          <p className="text-sm text-muted-foreground">
            Pages the automated quality check could not verify. These documents are not used for
            search or drafting until every flagged page is approved.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
          <RefreshCw className="mr-2 h-4 w-4" /> Refresh
        </Button>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading...
        </div>
      ) : error ? (
        <p className="text-sm text-red-700">{error}</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No documents need extraction review.</p>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">{total} document(s)</p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Document</TableHead>
                <TableHead>Project</TableHead>
                <TableHead>Review pages</TableHead>
                <TableHead>Reason</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Uploaded</TableHead>
                <TableHead>Processed</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow key={item.document_id}>
                  <TableCell>
                    <div className="font-medium">{item.letter_no || item.filename || item.document_id}</div>
                    {item.subject && <div className="text-xs text-muted-foreground">{item.subject}</div>}
                  </TableCell>
                  <TableCell className="text-xs">{item.project_id || "Organisation"}</TableCell>
                  <TableCell>
                    {item.review_pages.length} ({item.review_pages.join(", ")})
                  </TableCell>
                  <TableCell className="max-w-xs text-xs">{item.review_reason || "-"}</TableCell>
                  <TableCell className="text-xs">{item.processing_status}</TableCell>
                  <TableCell className="text-xs">{formatDate(item.uploaded_at)}</TableCell>
                  <TableCell className="text-xs">{formatDate(item.updated_at)}</TableCell>
                  <TableCell>
                    <Button size="sm" onClick={() => open(item)}>
                      Review
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </div>
  );
};

export default ExtractionReviewQueuePage;
