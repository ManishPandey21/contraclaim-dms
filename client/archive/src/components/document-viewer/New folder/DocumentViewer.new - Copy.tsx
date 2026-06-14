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

import "@react-pdf-viewer/core/lib/styles/index.css";
import "@react-pdf-viewer/default-layout/lib/styles/index.css";
import "@react-pdf-viewer/search/lib/styles/index.css";
import "@react-pdf-viewer/zoom/lib/styles/index.css";

import { Document } from "../../pages/DocumentViewerPage";

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
    const loadPdf = async () => {
      setLoading(true);
      setError(null);
      try {
        if (id) {
          const token = localStorage.getItem("accessToken");
          if (!token) {
            throw new Error("No authentication token found");
          }

          const response = await fetch(`/api/documents/${id}`, {
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
              throw new Error("Session expired. Please login again");
            } else if (response.status === 403) {
              throw new Error(
                "You do not have permission to view this document"
              );
            } else {
              throw new Error(`Failed to fetch document: ${response.status}`);
            }
          }
          const documentData = await response.json();
          if (documentData.presigned_url) {
            setPdfUrl(documentData.presigned_url);
          } else {
            setPdfUrl(null);
          }
        } else if (!id) {
          setPdfUrl(null);
        }
      } catch (err) {
        console.error("Error loading PDF:", err);
        setError(
          err instanceof Error ? err : new Error("Unknown error loading PDF")
        );
      } finally {
        setLoading(false);
      }
    };

    loadPdf();
  }, [document.id, isPdfDocument, id]);

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
          <div className="relative w-64 flex">
            <SearchComponent>
              {(renderSearchProps) => (
                <div className="flex-1 relative">
                  <Search className="absolute left-2 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="Search in document..."
                    className="pl-8 h-8 text-sm"
                    value={renderSearchProps.keyword}
                    onChange={(e) =>
                      renderSearchProps.setKeyword(e.target.value)
                    }
                    onKeyDown={(e) =>
                      e.key === "Enter" && renderSearchProps.search()
                    }
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={renderSearchProps.search}
                    className="ml-2"
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
              <Worker
                workerUrl={`https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js`}
              >
                <div style={{ height: "100%" }}>
                  <Viewer
                    fileUrl={pdfUrl}
                    plugins={[
                      defaultLayoutPluginInstance,
                      searchPluginInstance,
                      zoomPluginInstance,
                    ]}
                    defaultScale={SpecialZoomLevel.PageFit}
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
