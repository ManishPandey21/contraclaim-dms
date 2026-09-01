import { api } from "./api";

export interface DocumentItem {
  _id: string;
  name?: string;
  filename?: string;
  subject?: string | null;
  upload_id?: string;
  organization_id?: string;
  project_id?: string | null;
  uploadType?: string;
  categories?: string[] | null;
  file_path?: string | null;
  saved_path?: string | null;
  size?: number | null;
  status?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export type ListDocumentsParams = {
  organization_id?: string;
  project_id?: string;
  limit?: number;
  skip?: number;
  uploadType?: string; // e.g., "contract"
  q?: string; // optional search term (server dependent)
  sort?: string; // optional sort spec (server dependent)
};

export async function listDocuments(params?: ListDocumentsParams) {
  const { data } = await api.get("/document-search", { params });
  return data as {
    documents: DocumentItem[];
    total?: number;
    limit?: number;
    skip?: number;
  };
}

export async function getDocument(id: string) {
  const { data } = await api.get(`/documents/${id}`);
  return data as DocumentItem;
}

/**
 * Download a document by upload_id (preferred) or fallback to file_path.
 * Returns the resolved filename for UI use.
 */
export async function downloadDocumentFile(opts: {
  upload_id?: string;
  file_path?: string;
  filenameFallback?: string;
}) {
  if (!opts.upload_id && !opts.file_path) {
    throw new Error("upload_id or file_path is required");
  }
  const resp = await api.get("/contracts/download", {
    params: { upload_id: opts.upload_id, file_path: opts.file_path },
    responseType: "blob",
  });

  // Try to parse filename from Content-Disposition
  const disposition = (resp.headers as any)?.["content-disposition"] as
    | string
    | undefined;
  let filename = opts.filenameFallback || "document";
  if (disposition) {
    const m = disposition.match(/filename\*?=(?:UTF-8''|")?([^\";]+)/i);
    if (m && m[1]) {
      try {
        filename = decodeURIComponent(m[1].replace(/\"/g, ""));
      } catch {
        filename = m[1].replace(/\"/g, "");
      }
    }
  }

  const blob = resp.data instanceof Blob ? resp.data : new Blob([resp.data]);
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);

  return filename;
}
