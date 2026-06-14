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
  const {
    parties,
    loading,
    createParty: createPartyService,
    deleteParty,
    refreshParties,
  } = useParties();
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

  // Party actions with proper type handling
  const createParty = useCallback(
    async (data: PartyFormData) => {
      try {
        const partyData: Omit<Party, "id" | "createdAt" | "representatives"> = {
          name: data.name,
          type: data.type,
          contactEmail: data.contactEmail || undefined,
          contactPhone: data.contactPhone || undefined,
          projects: data.projects || [],
          address: data.address,
          city: data.city,
          state: data.state,
          pinCode: data.pinCode,
          country: data.country,
        };
        return await createPartyService(partyData);
      } catch (error) {
        console.error("Failed to create party:", error);
        return undefined;
      }
    },
    [createPartyService]
  );

  const updateParty = useCallback(
    async (id: string, data: Partial<PartyFormData>) => {
      try {
        const existingParty = parties.find((p) => p.id === id);
        if (!existingParty) {
          throw new Error("Party not found");
        }

        const updatedData: Omit<Party, "id" | "createdAt" | "representatives"> =
          {
            name: data.name || existingParty.name,
            type: data.type || existingParty.type,
            contactEmail: data.contactEmail ?? existingParty.contactEmail,
            contactPhone: data.contactPhone ?? existingParty.contactPhone,
            projects: data.projects || existingParty.projects,
            address: data.address ?? existingParty.address,
            city: data.city ?? existingParty.city,
            state: data.state ?? existingParty.state,
            pinCode: data.pinCode ?? existingParty.pinCode,
            country: data.country ?? existingParty.country,
          };

        const updatedParty = await createPartyService(updatedData);
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
    [createPartyService, selectedParty, refreshParties, parties]
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
