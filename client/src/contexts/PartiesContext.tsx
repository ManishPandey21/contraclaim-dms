import React, {
  createContext,
  useContext,
  useState,
  useCallback,
  ReactNode,
} from "react";
import { Party, Representative, Concern } from "../types/api";
import { useParties, useConcerns } from "../services/parties-service";
import {
  usePartySearch,
  usePartyTypeFilter,
  usePartySorting,
} from "../hooks/use-party-search";
import {
  PartyFormData,
  RepresentativeFormData,
  ConcernFormData,
} from "../schemas/party-schemas";

interface PartiesContextType {
  // State
  parties: Party[];
  selectedParty: Party | null;
  concerns: Concern[];
  loading: boolean;
  searchTerm: string;
  partyTypeFilter: "Organization" | "Individual" | undefined;

  // Party actions
  setSelectedParty: (party: Party | null) => void;
  createParty: (data: PartyFormData) => Promise<Party | undefined>;
  updateParty: (
    id: string,
    data: Partial<PartyFormData>
  ) => Promise<Party | undefined>;
  deleteParty: (id: string) => Promise<void>;

  // Representative actions
  addRepresentative: (
    partyId: string,
    data: RepresentativeFormData
  ) => Promise<Representative | undefined>;
  deleteRepresentative: (
    partyId: string,
    representativeId: string
  ) => Promise<void>;

  // Concern actions
  createConcern: (data: ConcernFormData) => Promise<Concern | undefined>;
  deleteConcern: (id: string) => Promise<void>;

  // Filters and search
  setSearchTerm: (term: string) => void;
  setPartyTypeFilter: (type: "Organization" | "Individual" | undefined) => void;

  // Computed values
  filteredParties: Party[];
  organizationCount: number;
  individualCount: number;
  hasSearchResults: boolean;
}

const PartiesContext = createContext<PartiesContextType | undefined>(undefined);

export const PartiesProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  // State management hooks
  const { parties, loading, createParty, deleteParty, refreshParties } =
    useParties();
  const { concerns, createConcern, deleteConcern } = useConcerns();

  // Local state
  const [selectedParty, setSelectedParty] = useState<Party | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [partyTypeFilter, setPartyTypeFilter] = useState<
    "Organization" | "Individual" | undefined
  >();

  // Search and filter hooks
  const { filteredParties: searchResults, hasResults } = usePartySearch(
    parties,
    searchTerm
  );
  const {
    filteredParties: typeFilteredParties,
    organizationCount,
    individualCount,
  } = usePartyTypeFilter(searchResults, partyTypeFilter);
  const { sortedParties } = usePartySorting(typeFilteredParties);

  // Party actions
  const updateParty = useCallback(
    async (id: string, data: Partial<PartyFormData>) => {
      try {
        const updatedParty = await createParty({ ...(data as PartyFormData) });
        if (updatedParty && selectedParty?.id === id) {
          setSelectedParty(updatedParty);
        }
        await refreshParties();
        return updatedParty;
      } catch (error) {
        console.error("Failed to update party:", error);
        return undefined;
      }
    },
    [createParty, selectedParty, refreshParties]
  );

  // Representative actions
  const addRepresentative = useCallback(
    async (partyId: string, data: RepresentativeFormData) => {
      try {
        // Implementation will be added when we integrate with the API
        return undefined;
      } catch (error) {
        console.error("Failed to add representative:", error);
        return undefined;
      }
    },
    []
  );

  const deleteRepresentative = useCallback(
    async (partyId: string, representativeId: string) => {
      try {
        // Implementation will be added when we integrate with the API
      } catch (error) {
        console.error("Failed to delete representative:", error);
      }
    },
    []
  );

  const value: PartiesContextType = {
    // State
    parties,
    selectedParty,
    concerns,
    loading,
    searchTerm,
    partyTypeFilter,

    // Actions
    setSelectedParty,
    createParty,
    updateParty,
    deleteParty,
    addRepresentative,
    deleteRepresentative,
    createConcern,
    deleteConcern,

    // Filters and search
    setSearchTerm,
    setPartyTypeFilter,

    // Computed values
    filteredParties: sortedParties,
    organizationCount,
    individualCount,
    hasSearchResults: hasResults,
  };

  return (
    <PartiesContext.Provider value={value}>{children}</PartiesContext.Provider>
  );
};

export const usePartiesContext = () => {
  const context = useContext(PartiesContext);
  if (context === undefined) {
    throw new Error("usePartiesContext must be used within a PartiesProvider");
  }
  return context;
};

// Helper hooks for specific functionality
export const usePartyOperations = () => {
  const { createParty, updateParty, deleteParty } = usePartiesContext();
  return { createParty, updateParty, deleteParty };
};

export const useRepresentativeOperations = () => {
  const { addRepresentative, deleteRepresentative } = usePartiesContext();
  return { addRepresentative, deleteRepresentative };
};

export const useConcernOperations = () => {
  const { createConcern, deleteConcern } = usePartiesContext();
  return { createConcern, deleteConcern };
};

export const usePartyFilters = () => {
  const {
    searchTerm,
    setSearchTerm,
    partyTypeFilter,
    setPartyTypeFilter,
    filteredParties,
    organizationCount,
    individualCount,
    hasSearchResults,
  } = usePartiesContext();

  return {
    searchTerm,
    setSearchTerm,
    partyTypeFilter,
    setPartyTypeFilter,
    filteredParties,
    organizationCount,
    individualCount,
    hasSearchResults,
  };
};
