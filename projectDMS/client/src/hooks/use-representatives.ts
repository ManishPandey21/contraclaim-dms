import { useState, useCallback } from "react";
import { Representative } from "../types/api";
import { PartiesService } from "../services/parties-service";
import { useAuthenticatedApi } from "./use-auth";
import { toast } from "sonner";

export const useRepresentatives = (partyId: string) => {
  const [representatives, setRepresentatives] = useState<Representative[]>([]);
  const [loading, setLoading] = useState(false);
  const { executeRequest } = useAuthenticatedApi();

  const addRepresentative = async (
    representative: Omit<Representative, "id" | "partyId">
  ) => {
    try {
      setLoading(true);
      const newRepresentative = await executeRequest(() =>
        PartiesService.addRepresentative(partyId, representative)
      );

      if (newRepresentative) {
        setRepresentatives((prev) => [...prev, newRepresentative]);
        toast.success("Representative added successfully");
        return newRepresentative;
      }
    } catch (error) {
      toast.error("Failed to add representative");
      throw error;
    } finally {
      setLoading(false);
    }
  };

  const deleteRepresentative = async (representativeId: string) => {
    try {
      setLoading(true);
      await executeRequest(() =>
        PartiesService.deleteRepresentative(partyId, representativeId)
      );

      setRepresentatives((prev) =>
        prev.filter((r) => r.id !== representativeId)
      );
      toast.success("Representative removed successfully");
    } catch (error) {
      toast.error("Failed to remove representative");
      throw error;
    } finally {
      setLoading(false);
    }
  };

  const updateRepresentatives = useCallback(
    (newRepresentatives: Representative[]) => {
      setRepresentatives(newRepresentatives);
    },
    []
  );

  return {
    representatives,
    loading,
    addRepresentative,
    deleteRepresentative,
    updateRepresentatives,
  };
};
