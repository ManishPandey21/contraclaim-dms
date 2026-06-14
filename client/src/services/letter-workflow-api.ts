import { api } from "./api";

export interface ContextDocumentSummary {
  id: string;
  letterNo?: string;
  subject?: string;
  uploadType?: string;
  date?: string;
  project_id?: string;
  organization_id?: string;
}

export interface ContextDocumentsResponse {
  document_ids: string[];
  documents: ContextDocumentSummary[];
  suggested_documents?: ContextDocumentSummary[];
}

export async function fetchContextDocuments(
  letterId: string
): Promise<ContextDocumentsResponse> {
  const { data } = await api.get<ContextDocumentsResponse>(
    `/letters/${letterId}/context-documents`
  );
  return data;
}

export async function saveContextDocuments(
  letterId: string,
  documentIds: string[]
): Promise<ContextDocumentsResponse> {
  const { data } = await api.put<ContextDocumentsResponse>(
    `/letters/${letterId}/context-documents`,
    {
      document_ids: documentIds,
    }
  );
  return data;
}
