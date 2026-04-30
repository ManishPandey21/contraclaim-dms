import React, { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { FileText, Search, AlertTriangle, Loader2 } from "lucide-react";
import { useParams } from "react-router-dom";
import { Viewer, Worker, SpecialZoomLevel } from "@react-pdf-viewer/core";
import { defaultLayoutPlugin } from "@react-pdf-viewer/default-layout";
import { searchPlugin } from "@react-pdf-viewer/search";
import { zoomPlugin } from "@react-pdf-viewer/zoom";
import { joinApiUrl } from "@/config/api";

import "@react-pdf-viewer/core/lib/styles/index.css";
import "@react-pdf-viewer/default-layout/lib/styles/index.css";
import "@react-pdf-viewer/search/lib/styles/index.css";
import "@react-pdf-viewer/zoom/lib/styles/index.css";

import { LocalDocument as Document } from "../../pages/DocumentViewerPage";

interface DocumentViewerProps {
  document: Document;
}

interface ZoomControlsProps {
  zoomPluginInstance: ReturnType<typeof zoomPlugin>;
}

const ZoomControls: React.FC<ZoomControlsProps> = ({ zoomPluginInstance }) => {
  const { ZoomIn, ZoomOut, CurrentScale } = zoomPluginInstance;

  return (
    <div className="flex items-center space-x-2">
      <ZoomOut>
        {(props: { onClick: () => void }) => (
          <Button
            variant="ghost"
            size="icon"
            onClick={props.onClick}
            aria-label="Zoom Out"
          >
            <span>-</span>
          </Button>
        )}
      </ZoomOut>
      <div className="w-16 text-center">
        <CurrentScale />
      </div>
      <ZoomIn>
        {(props: { onClick: () => void }) => (
          <Button
            variant="ghost"
            size="icon"
            onClick={props.onClick}
            aria-label="Zoom In"
          >
            <span>+</span>
          </Button>
        )}
      </ZoomIn>
    </div>
  );
};

const DocumentViewer: React.FC<DocumentViewerProps> = ({ document }) => {
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const { id } = useParams();

  const isPdfDocument = document.filename?.toLowerCase().endsWith(".pdf");
  const defaultLayoutPluginInstance = defaultLayoutPlugin();
  const searchPluginInstance = searchPlugin();
  const zoomPluginInstance = zoomPlugin();

  useEffect(() => {
    const localPdfPath = "/letter1.pdf";
    const loadPdf = async () => {
      setLoading(true);
      setError(null);
      try {
        if (id) {
          const token = localStorage.getItem("accessToken");
          if (!token) {
            console.warn("No authentication token found, loading local PDF");
            setPdfUrl(localPdfPath);
            return;
          }

          const response = await fetch(joinApiUrl(`/documents/${id}`), {
            headers: {
              Authorization: `Bearer ${token}`,
            },
          });

          if (!response.ok) {
            const errorText = await response.text();
            console.error("API Error:", {
              status: response.status,
              statusText: response.statusText,
              body: errorText,
              headers: Object.fromEntries(response.headers.entries()),
            });

            if (response.status === 401) {
              console.warn("Session expired. Loading local PDF");
              setPdfUrl(localPdfPath);
              return;
            } else if (response.status === 403) {
              console.warn("Permission denied. Loading local PDF");
              setPdfUrl(localPdfPath);
              return;
            } else {
              console.warn(
                `Failed to fetch document: ${response.status}. Loading local PDF`
              );
              setPdfUrl(localPdfPath);
              return;
            }
          }

          const documentData = await response.json();
          const rawUrl = documentData.presigned_url as string | undefined;
          const downloadApi = joinApiUrl(`/documents/${id}/download`);

          const isLikelySigned = (url: string): boolean => {
            try {
              const u = new URL(url, window.location.origin);
              const qp = u.searchParams;
              const keys = [
                "X-Amz-Algorithm",
                "X-Amz-Credential",
                "X-Amz-Signature",
                "X-Amz-Security-Token",
                "x-amz-algorithm",
                "x-amz-credential",
                "x-amz-signature",
              ];
              return keys.some((k) => qp.has(k));
            } catch {
              return false;
            }
          };

          const resolveUrl = (u?: string): string | null => {
            if (!u || typeof u !== "string") return null;
            const lower = u.toLowerCase();
            if (lower.startsWith("http://") || lower.startsWith("https://")) {
              return u;
            } else if (u.startsWith("/api/")) {
              return u;
            } else if (u.startsWith("/")) {
              return joinApiUrl(u);
            } else {
              return joinApiUrl(`/${u}`);
            }
          };

          let candidate = resolveUrl(rawUrl);

          if (candidate && isLikelySigned(candidate)) {
            // Likely a valid pre-signed URL; let the viewer fetch it directly
            setPdfUrl(candidate);
          } else {
            // Fetch via authenticated backend endpoint, then serve as a Blob URL to the viewer
            try {
              const fileResp = await fetch(downloadApi, {
                headers: { Authorization: `Bearer ${token}` },
              });
              if (fileResp.ok) {
                const blob = await fileResp.blob();
                const objectUrl = URL.createObjectURL(blob);
                setPdfUrl(objectUrl);
              } else if (candidate) {
                // Try the candidate anyway (may be public)
                setPdfUrl(candidate);
              } else {
                setPdfUrl(localPdfPath);
              }
            } catch (e) {
              if (candidate) {
                setPdfUrl(candidate);
              } else {
                setPdfUrl(localPdfPath);
              }
            }
          }
        } else if (!id) {
          setPdfUrl(localPdfPath);
        }
      } catch (err) {
        console.error("Error loading PDF:", err);
        setPdfUrl(localPdfPath);
      } finally {
        // Small delay to allow pdf.worker to initialize and file to become available
        await new Promise((r) => setTimeout(r, 500));
        setLoading(false);
      }
    };

    loadPdf();
  }, [document.id, isPdfDocument, id]);
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

  const { Search: SearchComponent } = searchPluginInstance;

  return (
    <div className="h-full flex flex-col">
      <div className="bg-white border-b flex justify-between items-center py-2 px-4">
        <div className="flex items-center space-x-4">
          {isPdfDocument && pdfUrl && (
            <ZoomControls zoomPluginInstance={zoomPluginInstance} />
          )}
        </div>

        {isPdfDocument && pdfUrl && (
          <div className="flex items-center space-x-2">
            <SearchComponent>
              {(renderSearchProps) => (
                <div className="flex items-center">
                  <div className="relative">
                    <Search className="absolute left-2 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      placeholder="Search in document..."
                      className="pl-8 h-8 text-sm w-48"
                      value={renderSearchProps.keyword}
                      onChange={(e) =>
                        renderSearchProps.setKeyword(e.target.value)
                      }
                      onKeyDown={(e) =>
                        e.key === "Enter" && renderSearchProps.search()
                      }
                    />
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={renderSearchProps.search}
                    className="h-8 ml-2"
                    aria-label="Search Document"
                  >
                    Search
                  </Button>
                </div>
              )}
            </SearchComponent>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-auto bg-gray-900 flex items-center justify-center">
        {isPdfDocument && pdfUrl ? (
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
            ) : (
              <Worker workerUrl={`${window.location.origin}/pdf.worker.min.js`}>
                <div style={{ height: "100%" }}>
                  <Viewer
                    fileUrl={pdfUrl}
                    plugins={[
                      defaultLayoutPluginInstance,
                      searchPluginInstance,
                      zoomPluginInstance,
                    ]}
                    defaultScale={SpecialZoomLevel.PageFit}
                    renderError={(error: Error) => (
                      <div
                        style={{
                          padding: "1rem",
                          color: "red",
                          textAlign: "center",
                        }}
                      >
                        <p>
                          <strong>Viewer Error:</strong> {error.message}
                        </p>
                        <p>
                          Could not load the PDF. Check the file URL and the
                          worker script.
                        </p>
                        {/* {console.error("React PDF Viewer Error:", error)} */}
                      </div>
                    )}
                  />
                </div>
              </Worker>
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
                      ? `File type: ${
                          document.filename?.split(".").pop()?.toUpperCase() ||
                          "Unknown"
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
