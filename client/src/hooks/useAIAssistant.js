import { useState, useCallback } from "react";
import { api } from "@/services/api";

export const useAIAssistant = () => {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [aiHistory, setAiHistory] = useState([]);

  const searchSimilarLetters = useCallback(async (query, limit = 3) => {
    setIsLoading(true);
    setError(null);
    try {
      const { data } = await api.post("/ai-assistant/search-letters", {
        query,
        limit,
      });
      return data;
    } catch (err) {
      setError("Failed to search similar letters");
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const generateEnhancedDraft = useCallback(async (request) => {
    setIsLoading(true);
    setError(null);
    try {
      const orgId = window.localStorage.getItem("org_id") || undefined;
      const projId = window.localStorage.getItem("proj_id") || undefined;
      const payload = {
        ...request,
        organization_id: orgId,
        project_id: projId,
      };
      const { data } = await api.post("/ai-assistant/enhanced-draft", payload);
      return data;
    } catch (err) {
      setError(
        err?.response?.data?.detail || "Failed to generate enhanced draft"
      );
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const extractMetadata = useCallback(async (file) => {
    setIsLoading(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const { data } = await api.post(
        "/ai-assistant/extract-metadata",
        formData,
        {
          headers: { "Content-Type": "multipart/form-data" },
        }
      );
      return data;
    } catch (err) {
      setError("Failed to extract metadata");
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const getAIHistory = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const { data } = await api.get("/ai-assistant/letter-history");
      setAiHistory(data.drafts || []);
      return data;
    } catch (err) {
      setError("Failed to get AI history");
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    searchSimilarLetters,
    generateEnhancedDraft,
    extractMetadata,
    getAIHistory,
    isLoading,
    error,
    aiHistory,
  };
};
