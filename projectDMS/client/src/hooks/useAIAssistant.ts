import { useState, useCallback } from "react";
import { api } from "@/services/api";

export interface SimilarLetter {
  id: string;
  title: string;
  subject: string;
  content: string;
  similarity_score: number;
}

export interface SearchSimilarLettersResponse {
  similar_letters: SimilarLetter[];
  query: string;
}

export interface GenerateEnhancedDraftRequest {
  subject: string;
  recipient: string;
  user_id: string;
  context?: string;
  metadata?: any;
  // Pass org/project context explicitly to backend to avoid 'untitled' fallbacks
  organization_id?: string;
  project_id?: string;
}

export interface GenerateEnhancedDraftResponse {
  draft_letter: string;
  similar_letters: SimilarLetter[];
  metadata_used: any;
}

export interface ExtractMetadataResponse {
  subject?: string;
  recipient?: string;
  sender?: string;
  date?: string;
  reference_number?: string;
  key_points: string[];
  entities: string[];
}

export const useAIAssistant = () => {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const searchSimilarLetters = useCallback(
    async (query: string): Promise<SearchSimilarLettersResponse> => {
      setIsLoading(true);
      setError(null);
      try {
        const { data } = await api.post<SearchSimilarLettersResponse>(
          "/ai-assistant/search-similar-letters",
          { query }
        );
        return data;
      } catch (e: any) {
        setError(
          e?.response?.data?.detail || "Failed to search similar letters"
        );
        throw e;
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  const generateEnhancedDraft = useCallback(
    async (
      request: GenerateEnhancedDraftRequest
    ): Promise<GenerateEnhancedDraftResponse> => {
      setIsLoading(true);
      setError(null);
      try {
        const orgId = window.localStorage.getItem("org_id") || undefined;
        const projId = window.localStorage.getItem("proj_id") || undefined;
        const payload: GenerateEnhancedDraftRequest = {
          ...request,
          organization_id: request.organization_id ?? orgId,
          project_id: request.project_id ?? projId,
        };
        const { data } = await api.post<GenerateEnhancedDraftResponse>(
          "/ai-assistant/generate-enhanced-draft",
          payload
        );
        return data;
      } catch (e: any) {
        setError(
          e?.response?.data?.detail || "Failed to generate enhanced draft"
        );
        throw e;
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  const extractMetadata = useCallback(
    async (file: File): Promise<ExtractMetadataResponse> => {
      setIsLoading(true);
      setError(null);
      try {
        const formData = new FormData();
        formData.append("file", file);

        const { data } = await api.post<ExtractMetadataResponse>(
          "/ai-assistant/extract-metadata",
          formData,
          {
            headers: {
              "Content-Type": "multipart/form-data",
            },
          }
        );
        return data;
      } catch (e: any) {
        setError(e?.response?.data?.detail || "Failed to extract metadata");
        throw e;
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  const generateResponse = useCallback(
    async (
      letterId: string,
      context?: string
    ): Promise<{ response: string }> => {
      setIsLoading(true);
      setError(null);
      try {
        const { data } = await api.post<{ response: string }>(
          "/ai-assistant/generate-response",
          { letter_id: letterId, context }
        );
        return data;
      } catch (e: any) {
        setError(e?.response?.data?.detail || "Failed to generate response");
        throw e;
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  const summarizeLetter = useCallback(
    async (letterId: string): Promise<{ summary: string }> => {
      setIsLoading(true);
      setError(null);
      try {
        const { data } = await api.post<{ summary: string }>(
          "/ai-assistant/summarize-letter",
          { letter_id: letterId }
        );
        return data;
      } catch (e: any) {
        setError(e?.response?.data?.detail || "Failed to summarize letter");
        throw e;
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  return {
    isLoading,
    error,
    searchSimilarLetters,
    generateEnhancedDraft,
    extractMetadata,
    generateResponse,
    summarizeLetter,
  };
};
