import { api } from "./api";

// RAG / semantic-retrieval console API. Mirrors routers/retrieval_engine.py
// (/api/v1/retrieval/*). Scope (org/project) is authorized server-side.

export interface RetrievalFilters {
  org_id: string;
  project_id: string;
  document_id?: string | null;
  tags?: string[];
}

export interface RetrievalRequest {
  query: string;
  filters: RetrievalFilters;
  limit?: number;
  strategy?: "vanilla" | "hyde" | "rag_fusion";
  backend?: "auto" | "qdrant" | "mongo";
  answer_style?: string;
}

export interface RetrievalCitation {
  document_id: string;
  chunk_id: string;
  page?: number | null;
  score?: number | null;
  snippet: string;
  document_title?: string | null;
  file_name?: string | null;
  letter_no?: string | null;
}

export interface RagResponse {
  answer: string;
  citations: RetrievalCitation[];
  strategy_used: string;
  timings: Record<string, number>;
}

export interface SearchResponse {
  results: RetrievalCitation[];
  backend_used?: string;
  timings?: Record<string, number>;
}

export async function ragQuery(payload: RetrievalRequest): Promise<RagResponse> {
  const { data } = await api.post("/v1/retrieval/rag", payload);
  return data as RagResponse;
}

export async function semanticSearch(payload: RetrievalRequest): Promise<SearchResponse> {
  const { data } = await api.post("/v1/retrieval/search", payload);
  // Tolerate shape drift (results vs hits).
  const results = Array.isArray(data?.results)
    ? data.results
    : Array.isArray(data?.hits)
      ? data.hits
      : [];
  return { results, backend_used: data?.backend_used, timings: data?.timings };
}
