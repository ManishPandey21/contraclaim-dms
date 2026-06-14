import { api } from "./api";
import type { SearchFilters } from "@/components/search/AdvancedSearchFilters";

export interface SearchResult {
  _id: string;
  name: string;
  filename: string;
  content?: string;
  excerpt?: string;
  organization_id?: string;
  project_id?: string;
  uploadType?: string;
  categories?: string[];
  file_path?: string;
  size?: number;
  createdAt?: string;
  updatedAt?: string;
  score?: number;
  highlights?: string[];
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  page: number;
  limit: number;
  hasMore: boolean;
  facets?: {
    organizations: Array<{ _id: string; name: string; count: number }>;
    projects: Array<{ _id: string; name: string; count: number }>;
    categories: Array<{ name: string; count: number }>;
    fileTypes: Array<{ type: string; count: number }>;
  };
  searchTime?: number;
}

export interface SearchParams extends SearchFilters {
  page?: number;
  limit?: number;
  includeFacets?: boolean;
  includeContent?: boolean;
}

/**
 * Perform advanced document search with filters
 */
export async function searchDocuments(
  params: SearchParams
): Promise<SearchResponse> {
  const searchParams = new URLSearchParams();

  // Basic search parameters
  if (params.query) searchParams.append("q", params.query);
  if (params.page) searchParams.append("page", params.page.toString());
  if (params.limit) searchParams.append("limit", params.limit.toString());
  if (params.sortBy) searchParams.append("sort_by", params.sortBy);
  if (params.sortOrder) searchParams.append("sort_order", params.sortOrder);

  // Date range filters
  if (params.dateRange.from)
    searchParams.append("date_from", params.dateRange.from);
  if (params.dateRange.to) searchParams.append("date_to", params.dateRange.to);

  // Array filters
  params.fileTypes.forEach((type) => searchParams.append("file_types", type));
  params.organizations.forEach((org) =>
    searchParams.append("organizations", org)
  );
  params.projects.forEach((project) =>
    searchParams.append("projects", project)
  );
  params.categories.forEach((category) =>
    searchParams.append("categories", category)
  );

  // New filters
  if ((params as any).tag)
    searchParams.append("tags", (params as any).tag as string);
  if ((params as any).direction)
    searchParams.append("upload_type", (params as any).direction as string);

  // Additional options
  if (params.includeFacets) searchParams.append("include_facets", "true");
  if (params.includeContent) searchParams.append("include_content", "true");

  const { data } = await api.get(
    `/search/documents?${searchParams.toString()}`
  );
  return data as SearchResponse;
}

/**
 * Get search suggestions based on partial query
 */
export async function getSearchSuggestions(
  query: string,
  limit = 5
): Promise<string[]> {
  if (!query.trim()) return [];

  const { data } = await api.get("/search/suggestions", {
    params: { q: query, limit },
  });
  return data.suggestions || [];
}

/**
 * Get popular search terms
 */
export async function getPopularSearches(
  limit = 10
): Promise<Array<{ term: string; count: number }>> {
  const { data } = await api.get("/search/popular", {
    params: { limit },
  });
  return data.searches || [];
}

/**
 * Save search query for analytics
 */
export async function trackSearch(
  query: string,
  filters: Partial<SearchFilters>,
  resultCount: number
): Promise<void> {
  try {
    await api.post("/search/track", {
      query,
      filters,
      result_count: resultCount,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    // Non-critical, don't throw
    console.warn("Failed to track search:", error);
  }
}

/**
 * Get search analytics for admin users
 */
export async function getSearchAnalytics(dateRange?: {
  from: string;
  to: string;
}) {
  const params: any = {};
  if (dateRange?.from) params.date_from = dateRange.from;
  if (dateRange?.to) params.date_to = dateRange.to;

  const { data } = await api.get("/search/analytics", { params });
  return data;
}

/**
 * Perform semantic search using AI/vector similarity
 */
export async function semanticSearch(
  query: string,
  options: {
    limit?: number;
    threshold?: number;
    organizations?: string[];
    projects?: string[];
  } = {}
): Promise<SearchResponse> {
  const { data } = await api.post("/search/semantic", {
    query,
    ...options,
  });
  return data as SearchResponse;
}

/**
 * Search within a specific document
 */
export async function searchInDocument(
  documentId: string,
  query: string,
  options: {
    caseSensitive?: boolean;
    wholeWords?: boolean;
    regex?: boolean;
  } = {}
): Promise<{
  matches: Array<{
    page?: number;
    position: number;
    context: string;
    highlight: string;
  }>;
  total: number;
}> {
  const { data } = await api.post(`/search/documents/${documentId}`, {
    query,
    ...options,
  });
  return data;
}

/**
 * Get similar documents based on content
 */
export async function findSimilarDocuments(
  documentId: string,
  limit = 5
): Promise<SearchResult[]> {
  const { data } = await api.get(`/search/similar/${documentId}`, {
    params: { limit },
  });
  return data.results || [];
}

/**
 * Advanced full-text search with highlighting
 */
export async function fullTextSearch(
  query: string,
  options: {
    fields?: string[];
    boost?: Record<string, number>;
    fuzzy?: boolean;
    proximity?: number;
    organizations?: string[];
    projects?: string[];
    limit?: number;
    offset?: number;
  } = {}
): Promise<SearchResponse> {
  const { data } = await api.post("/search/fulltext", {
    query,
    ...options,
  });
  return data as SearchResponse;
}
