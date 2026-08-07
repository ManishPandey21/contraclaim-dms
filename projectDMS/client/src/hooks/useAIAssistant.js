import { useState, useCallback } from "react";
import { api } from "@/services/api";
import { useTenant } from "@/contexts/TenantContext";

export const useAIAssistant = () => {
  const { selectedOrganizationId, selectedProjectId } = useTenant();
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
      const payload = {
        ...request,
        organization_id: selectedOrganizationId || undefined,
        project_id: selectedProjectId || undefined,
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
  }, [selectedOrganizationId, selectedProjectId]);

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
