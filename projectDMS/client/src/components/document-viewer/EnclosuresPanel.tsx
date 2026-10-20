import React, { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { useDropzone } from "react-dropzone";
import { Button } from "@/components/ui/button";
import Badge, { type BadgeProps } from "@/components/ui/badge";
import { FileText, Trash, Paperclip, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import { enhancedApi, type Enclosure } from "@/services/enhanced-api";
import { formatDateTime } from "@/utils/datetime";
import { isTerminalProcessingStatus } from "@/utils/processingStatus";
import { childStatusText, relationshipLabel } from "@/utils/childDocument";

interface EnclosuresPanelProps {
  documentId: string; // Get document ID as a prop
  /** How often a child still processing is re-read; the viewer polls at 4 s too. */
  pollIntervalMs?: number;
}

/** What the enclosure list needs from a Phase 8 child Document. */
interface ChildState {
  status: string | null;
  relationship_type: string | null;
}

const statusVariant = (status: string | null): BadgeProps["variant"] => {
  switch ((status || "").toLowerCase()) {
    case "completed":
      return "success";
    case "human_review_required":
      return "warning";
    case "failed":
    case "dead_lettered":
      return "danger";
    case "stored_only":
    case "skipped":
      return "neutral";
    default:
      return "primary";
  }
};

const fileTypeLabel = (enclosure: Enclosure): string => {
  const subtype = (enclosure.filetype || "").split("/")[1] || "";
  if (subtype && subtype !== "octet-stream" && subtype.length <= 5) {
    return subtype.toUpperCase();
  }
  const extension = enclosure.filename.includes(".")
    ? enclosure.filename.split(".").pop() || ""
    : "";
  return extension ? extension.toUpperCase() : "File";
};

const fileSizeLabel = (bytes: number): string => {
  if (!bytes || bytes < 1024) return `${bytes || 0} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const upsertEnclosure = (list: Enclosure[], enclosure: Enclosure): Enclosure[] =>
  list.some((existing) => existing.id === enclosure.id)
    ? list.map((existing) => (existing.id === enclosure.id ? enclosure : existing))
    : [...list, enclosure];

const EnclosuresPanel: React.FC<EnclosuresPanelProps> = ({
  documentId,
  pollIntervalMs = 4000,
}) => {
  const [enclosures, setEnclosures] = useState<Enclosure[]>([]);
  const [children, setChildren] = useState<Record<string, ChildState>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Status of the parent's first-class children, joined on child_document_id.
  // A failure leaves the rows' status unknown rather than hiding the list.
  const fetchChildren = useCallback(async () => {
    if (!documentId) return;
    try {
      const rows = await enhancedApi.getDocumentChildren(documentId);
      setChildren(
        Object.fromEntries(
          (rows || []).map((row) => [
            String(row._id),
            {
              status: row.processing_status ?? null,
              relationship_type: row.relationship_type ?? null,
            },
          ])
        )
      );
    } catch (error) {
      console.error("Error fetching child documents:", error);
    }
  }, [documentId]);

  // Canonical fetch function (reused after upload/delete and via Refresh button)
  const fetchEnclosures = useCallback(async () => {
    if (!documentId) return;
    setLoading(true);
    setError(null);
    try {
      const [data] = await Promise.all([
        enhancedApi.getDocumentEnclosures(documentId),
        fetchChildren(),
      ]);
      setEnclosures(data || []);
    } catch (error: any) {
      console.error("Error fetching enclosures:", error);
      setError(error.message || "Failed to fetch enclosures");
      toast.error("Failed to fetch enclosures");
    } finally {
      setLoading(false);
    }
  }, [documentId, fetchChildren]);

  // Initial fetch and on document change
  useEffect(() => {
    fetchEnclosures();
  }, [fetchEnclosures]);

  // Re-read children while any is still in flight; stop on the same terminal
  // states the viewer's own processing poll uses.
  const childInFlight = enclosures.some((enclosure) => {
    const child = enclosure.child_document_id
      ? children[enclosure.child_document_id]
      : undefined;
    return Boolean(child) && !isTerminalProcessingStatus(child?.status);
  });
  useEffect(() => {
    if (!childInFlight) return;
    const interval = window.setInterval(() => void fetchChildren(), pollIntervalMs);
    return () => window.clearInterval(interval);
  }, [childInFlight, fetchChildren, pollIntervalMs]);

  // Handle file drop
  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      if (!documentId) return;

      setLoading(true);
      setError(null);

      try {
        for (const file of acceptedFiles) {
          // The response is an envelope: the entry plus the child it became.
          // A retried upload answers with the same entry, so upsert by id.
          const response = await enhancedApi.addDocumentEnclosure(documentId, file);
          setEnclosures((prev) => upsertEnclosure(prev, response.enclosure));
          const childId = response.child_document_id;
          if (childId) {
            setChildren((prev) => ({
              ...prev,
              [childId]: {
                status: response.processing_status ?? null,
                relationship_type: response.relationship_type ?? null,
              },
            }));
          }
          toast.success(`${file.name} uploaded successfully`);
        }

        // After processing all files, re-fetch to ensure canonical list and fresh presigned URLs
        await fetchEnclosures();
      } catch (error: any) {
        console.error("Error uploading enclosure:", error);
        setError(error.message || "Failed to upload enclosure");
        toast.error("Failed to upload enclosure");
      } finally {
        setLoading(false);
      }
    },
    [documentId, fetchEnclosures]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
  });

  // Handle deletion of a historical (file-only) enclosure. A first-class child
  // is never offered this: the route only removes the parent's entry and would
  // orphan a live child Document. Child delete/restore needs the follow-up
  // child lifecycle route.
  const handleDeleteEnclosure = async (enclosureId: string) => {
    if (!documentId) return;

    setLoading(true);
    setError(null);

    try {
      const response = await authenticatedFetch(
        joinApiUrl(`/documents/${documentId}/enclosures/${enclosureId}`),
        {
          method: "DELETE",
        }
      );

      if (!response.ok) {
        let errorMessage = `Failed to delete enclosure: ${response.status}`;
        try {
          const errorData = await response.json();
          errorMessage = errorData.detail || errorMessage;
        } catch {
          // If response is not JSON, use status text
          errorMessage = `Failed to delete enclosure: ${response.statusText}`;
        }
        throw new Error(errorMessage);
      }

      // Optimistic update
      setEnclosures((prevEnclosures) =>
        prevEnclosures.filter((enclosure) => enclosure.id !== enclosureId)
      );

      // Re-fetch to confirm canonical state
      await fetchEnclosures();

      toast.success("Enclosure deleted successfully");
    } catch (error: any) {
      console.error("Error deleting enclosure:", error);
      setError(error.message || "Failed to delete enclosure");
      toast.error("Failed to delete enclosure");
    } finally {
      setLoading(false);
    }
  };

  const renderChildRow = (enclosure: Enclosure, childId: string) => {
    const child = children[childId];
    const status = child?.status ?? null;
    const statusText = childStatusText(status);
    return (
      <>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={statusVariant(status)}>{statusText}</Badge>
          <div className="ml-auto flex flex-wrap items-center gap-1">
            {status === "human_review_required" && (
              <Button asChild size="sm" variant="outline" className="h-7 text-xs">
                <Link
                  to={`/documentviewer/${childId}?tab=review`}
                  aria-label={`Review ${enclosure.filename}`}
                >
                  Review
                </Link>
              </Button>
            )}
            <Button asChild size="sm" variant="outline" className="h-7 text-xs">
              {/* The viewer route is not keyed by id, so name the tab: the
                  parent's Enclosures tab would otherwise carry over. */}
              <Link
                to={`/documentviewer/${childId}?tab=metadata`}
                aria-label={`Open document ${enclosure.filename}`}
              >
                Open
              </Link>
            </Button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground break-words">
          {relationshipLabel(child?.relationship_type)} · {fileTypeLabel(enclosure)} ·{" "}
          {fileSizeLabel(enclosure.filesize)}
        </p>
      </>
    );
  };

  // Historical entry: a stored file only. No child status is invented.
  const renderHistoricalRow = (enclosure: Enclosure) => (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <div className="ml-auto flex flex-wrap items-center gap-1">
          {enclosure.presigned_url && (
            <Button asChild size="sm" variant="outline" className="h-7 text-xs">
              <a
                href={enclosure.presigned_url}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`Open file ${enclosure.filename}`}
              >
                Open file
              </a>
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-red-500 hover:text-red-700 hover:bg-red-50"
            onClick={() => handleDeleteEnclosure(enclosure.id)}
            aria-label={`Delete enclosure ${enclosure.filename}`}
          >
            <Trash className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground break-words">
        {fileTypeLabel(enclosure)} · {fileSizeLabel(enclosure.filesize)}
      </p>
    </>
  );

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap justify-between items-center gap-2 mb-3">
        <h3 className="font-medium text-sm">Enclosures</h3>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs"
          onClick={fetchEnclosures}
          title="Refresh enclosures"
          aria-label="Refresh enclosures"
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 xl:mr-1 animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5 xl:mr-1" aria-hidden="true" />
          )}
          {/* Icon only below xl, where the metadata panel can be ~110 px wide. */}
          <span className="hidden xl:inline">Refresh</span>
        </Button>
      </div>

      {error && (
        <p role="alert" className="text-sm text-red-600 break-words">
          {error}
        </p>
      )}

      {loading && enclosures.length === 0 ? (
        <div className="flex justify-center py-4">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading enclosures" />
        </div>
      ) : enclosures.length === 0 ? (
        <p className="text-center py-4 text-sm text-muted-foreground">
          No enclosures attached to this document.
        </p>
      ) : (
        <ul className="space-y-2">
          {enclosures.map((enclosure) => (
            <li key={enclosure.id} className="min-w-0 rounded-md border p-2 space-y-1">
              <div className="flex min-w-0 items-center gap-2">
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span
                  className="min-w-0 truncate text-sm font-medium"
                  title={enclosure.filename}
                >
                  {enclosure.filename}
                </span>
              </div>
              {enclosure.child_document_id
                ? renderChildRow(enclosure, enclosure.child_document_id)
                : renderHistoricalRow(enclosure)}
              <p className="text-xs text-muted-foreground break-words">
                Uploaded {formatDateTime(enclosure.uploadedAt)}
              </p>
            </li>
          ))}
        </ul>
      )}

      <div
        {...getRootProps({
          role: "button",
          "aria-label": "Upload enclosures: drag and drop files here, or press Enter to browse",
        })}
        className={`p-4 border rounded-lg flex flex-col items-center justify-center text-center space-y-2 cursor-pointer transition ${
          isDragActive ? "bg-blue-50 border-blue-200" : "hover:bg-slate-50"
        }`}
      >
        <input {...getInputProps()} />
        <Paperclip className="h-10 w-10 text-muted-foreground mb-2" aria-hidden="true" />
        <h4 className="font-medium">Drag &amp; Drop</h4>
        <p className="text-sm text-muted-foreground">
          Drag and drop files here to attach them to this document, or click to
          browse.
        </p>
      </div>
    </div>
  );
};

export default EnclosuresPanel;
