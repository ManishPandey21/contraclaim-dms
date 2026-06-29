import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AlertTriangle, ExternalLink, FileText, Loader2 } from "lucide-react";
import { useParams } from "react-router-dom";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";

import type { LocalDocument as Document } from "../../pages/DocumentViewerPage";

interface DocumentViewerProps {
  document: Document;
}

const DocumentViewer: React.FC<DocumentViewerProps> = ({ document }) => {
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const { id } = useParams();

  const documentId = id || document.id || document._id;
  const isPdfDocument =
    (document.filetype?.toLowerCase()?.includes("pdf") ?? false) ||
    (document.filename?.toLowerCase()?.endsWith(".pdf") ?? false);

  const isLikelySignedUrl = useCallback((url: string): boolean => {
    try {
      const parsed = new URL(url, window.location.origin);
      const qp = parsed.searchParams;
      const signedKeys = [
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Signature",
        "X-Amz-Security-Token",
        "x-amz-algorithm",
        "x-amz-credential",
        "x-amz-signature",
        "AWSAccessKeyId",
        "Signature",
        "Expires",
      ];
      return signedKeys.some((key) => qp.has(key));
    } catch {
      return false;
    }
  }, []);

  const resolveUrl = useCallback((url?: string | null): string | null => {
    if (!url || typeof url !== "string") return null;
    const trimmed = url.trim();
    if (!trimmed) return null;

    const lower = trimmed.toLowerCase();
    if (lower.startsWith("http://") || lower.startsWith("https://")) {
      return trimmed;
    }
    if (trimmed.startsWith("/api/")) {
      return trimmed;
    }
    if (trimmed.startsWith("/")) {
      return joinApiUrl(trimmed);
    }
    return joinApiUrl(`/${trimmed}`);
  }, []);

  useEffect(() => {
    const localPdfPath = "/letter1.pdf";
    const loadPdf = async () => {
      setLoading(true);
      setError(null);

      if (!isPdfDocument) {
        setPdfUrl(null);
        setLoading(false);
        return;
      }

      try {
        const candidate = resolveUrl(document.presigned_url);

        if (candidate && isLikelySignedUrl(candidate)) {
          setPdfUrl(candidate);
          return;
        }

        if (!documentId) {
          setPdfUrl(localPdfPath);
          return;
        }

        const fileResp = await authenticatedFetch(
          joinApiUrl(`/documents/${documentId}/download`),
        );
        if (!fileResp.ok) {
          const errorText = await fileResp.text().catch(() => "");
          throw new Error(
            errorText ||
            `Failed to fetch PDF bytes (${fileResp.status} ${fileResp.statusText})`,
          );
        }

        const blob = await fileResp.blob();
        if (blob.size === 0) {
          throw new Error("The downloaded PDF file is empty");
        }
        const objectUrl = URL.createObjectURL(blob);
        setPdfUrl(objectUrl);
      } catch (err) {
        console.error("Error loading PDF:", err);
        setPdfUrl(null);
        setError(err instanceof Error ? err : new Error("Failed to load PDF"));
      } finally {
        setLoading(false);
      }
    };

    void loadPdf();
  }, [
    document.presigned_url,
    documentId,
    isLikelySignedUrl,
    isPdfDocument,
    resolveUrl,
  ]);
  // Cleanup any created Blob object URLs when pdfUrl changes or component unmounts
  useEffect(() => {
    return () => {
      try {
        if (pdfUrl && pdfUrl.startsWith("blob:")) {
          URL.revokeObjectURL(pdfUrl);
        }
      } catch {
        // ignore
      }
    };
  }, [pdfUrl]);

  const iframeUrl = useMemo(() => {
    if (!pdfUrl) return null;
    return `${pdfUrl}#toolbar=1&navpanes=0&view=FitH`;
  }, [pdfUrl]);

  const openPdf = useCallback(() => {
    if (!pdfUrl) return;
    window.open(pdfUrl, "_blank", "noopener,noreferrer");
  }, [pdfUrl]);

  return (
    <div className="h-full flex flex-col">
      <div className="bg-white border-b flex justify-between items-center py-2 px-4">
        <div className="flex items-center space-x-4">
          {isPdfDocument && (
            <span className="text-sm text-muted-foreground">
              PDF preview uses the browser's native viewer.
            </span>
          )}
        </div>

        {isPdfDocument && pdfUrl && (
          <div className="flex items-center space-x-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={openPdf}
              className="h-8 gap-2"
              aria-label="Open PDF in new tab"
            >
              <ExternalLink className="h-4 w-4" />
              Open
            </Button>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-auto bg-gray-900 flex items-center justify-center">
        {isPdfDocument ? (
          <div className="h-full w-full bg-white relative">
            {loading ? (
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="animate-pulse text-center">
                  <Loader2 className="h-12 w-12 text-gray-300 mx-auto mb-4 animate-spin" />
                  <p className="text-gray-500">Loading PDF document...</p>
                </div>
              </div>
            ) : error ? (
              <div className="absolute inset-0 flex items-center justify-center">
                <Alert variant="destructive" className="w-3/4">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertTitle>Error loading PDF</AlertTitle>
                  <AlertDescription>
                    There was a problem loading the PDF document.{" "}
                    {error.message}
                  </AlertDescription>
                </Alert>
              </div>
            ) : !pdfUrl ? (
              <div className="absolute inset-0 flex items-center justify-center">
                <Alert className="w-3/4">
                  <FileText className="h-4 w-4" />
                  <AlertTitle>PDF preview unavailable</AlertTitle>
                  <AlertDescription>
                    The file could not be resolved to a downloadable PDF URL.
                  </AlertDescription>
                </Alert>
              </div>
            ) : iframeUrl ? (
              <iframe
                title={document.filename || "PDF document"}
                src={iframeUrl}
                className="h-full w-full border-0 bg-white"
              />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center">
                <Alert className="w-3/4">
                  <FileText className="h-4 w-4" />
                  <AlertTitle>PDF preview unavailable</AlertTitle>
                  <AlertDescription>
                    The PDF URL could not be prepared for preview.
                  </AlertDescription>
                </Alert>
              </div>
            )}
          </div>
        ) : (
          <div
            className="bg-white shadow-lg mx-auto my-8"
            style={{
              width: "8.5in",
              height: "11in",
              position: "relative",
            }}
          >
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-center">
                <FileText className="h-20 w-20 text-gray-300 mx-auto mb-4" />
                <div>
                  <p className="text-gray-500">
                    {!isPdfDocument
                      ? "This is not a PDF document"
                      : "PDF preview not available"}
                  </p>
                  <p className="text-xs text-gray-400 mt-1">
                    {!isPdfDocument
                      ? `File type: ${document.filename?.split(".").pop()?.toUpperCase() ?? "Unknown"
                      }`
                      : "Document unavailable"}
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default DocumentViewer;
