import React, { useState, useEffect, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { FileText, Trash, Paperclip, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import { formatDateTime } from "@/utils/datetime";

interface Enclosure {
  id: string;
  filename: string;
  presigned_url: string;
  filetype: string;
  filesize: number;
  uploadedAt: string; // Or Date
  uploadedBy: string; // Or User object
}

interface EnclosuresPanelProps {
  documentId: string; // Get document ID as a prop
}

const EnclosuresPanel: React.FC<EnclosuresPanelProps> = ({ documentId }) => {
  const [enclosures, setEnclosures] = useState<Enclosure[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Canonical fetch function (reused after upload/delete and via Refresh button)
  const fetchEnclosures = useCallback(async () => {
    if (!documentId) return;
    setLoading(true);
    setError(null);
    try {
      const response = await authenticatedFetch(
        joinApiUrl(`/documents/${documentId}/enclosures`),
        {}
      );
      if (!response.ok) {
        let errorMessage = `Failed to fetch enclosures: ${response.status}`;
        try {
          const errorData = await response.json();
          errorMessage = errorData.detail || errorMessage;
        } catch {
          errorMessage = `Failed to fetch enclosures: ${response.statusText}`;
        }
        throw new Error(errorMessage);
      }
      const data: Enclosure[] = await response.json();
      setEnclosures(data);
    } catch (error: any) {
      console.error("Error fetching enclosures:", error);
      setError(error.message || "Failed to fetch enclosures");
      toast.error("Failed to fetch enclosures");
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  // Initial fetch and on document change
  useEffect(() => {
    fetchEnclosures();
  }, [fetchEnclosures]);

  // Handle file drop
  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      if (!documentId) return;

      setLoading(true);
      setError(null);

      try {
        for (const file of acceptedFiles) {
          const formData = new FormData();
          formData.append("file", file); // The backend expects a 'file' field

          const response = await authenticatedFetch(
            joinApiUrl(`/documents/${documentId}/enclosures`),
            {
              method: "POST",
              body: formData,
            }
          );

          if (!response.ok) {
            let errorMessage = `Failed to upload enclosure: ${response.status}`;
            try {
              const errorData = await response.json();
              errorMessage = errorData.detail || errorMessage;
            } catch {
              // If response is not JSON (e.g., HTML error page), use status text
              errorMessage = `Failed to upload enclosure: ${response.statusText}`;
            }
            throw new Error(errorMessage);
          }

          // Check if response is JSON before parsing
          const contentType = response.headers.get("content-type");
          if (!contentType || !contentType.includes("application/json")) {
            throw new Error(
              "Server returned non-JSON response. Please check the API endpoint."
            );
          }

          // Still parse for immediate optimistic feedback
          const newEnclosure: Enclosure = await response.json();
          setEnclosures((prevEnclosures) => [...prevEnclosures, newEnclosure]);
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

  // Handle enclosure deletion
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

  return (
    <div className="space-y-5">
      <div className="flex justify-between items-center mb-3">
        <h3 className="font-medium text-sm">Enclosures</h3>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs"
          onClick={fetchEnclosures}
          title="Refresh enclosures"
        >
          <RefreshCw className="h-3.5 w-3.5 mr-1" />
          Refresh
        </Button>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Filename</TableHead>
            <TableHead>Uploaded At</TableHead>
            <TableHead>Uploader</TableHead>
            <TableHead className="w-10"></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center py-4">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </TableCell>
            </TableRow>
          ) : error ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center py-4 text-red-500">
                {error}
              </TableCell>
            </TableRow>
          ) : enclosures.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={4}
                className="text-center py-4 text-muted-foreground"
              >
                No enclosures attached to this document.
              </TableCell>
            </TableRow>
          ) : (
            enclosures.map((enclosure) => (
              <TableRow key={enclosure.id}>
                <TableCell className="flex items-center gap-2">
                  <FileText className="h-4 w-4 text-muted-foreground" />
                  <a
                    href={enclosure.presigned_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-medium hover:underline"
                  >
                    {enclosure.filename}
                  </a>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDateTime(enclosure.uploadedAt)}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {enclosure.uploadedBy} {/*  Display uploader ID or name */}
                </TableCell>
                <TableCell>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-red-500 hover:text-red-700 hover:bg-red-50"
                    onClick={() => handleDeleteEnclosure(enclosure.id)}
                  >
                    <Trash className="h-3.5 w-3.5" />
                  </Button>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      <div
        {...getRootProps()}
        className={`p-4 border rounded-lg flex flex-col items-center justify-center text-center space-y-2 cursor-pointer transition ${
          isDragActive ? "bg-blue-50 border-blue-200" : "hover:bg-slate-50"
        }`}
      >
        <input {...getInputProps()} />
        <Paperclip className="h-10 w-10 text-muted-foreground mb-2" />
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
